import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from armored_core.coordinator import Coordinator
from armored_core.models import PublicationCheck, State
from armored_core.services import PublicationResult, VisionResult, VisionUnresolvedError



def _install_coordinator_speech_studio_test_seam(coordinator):
    """Inject deterministic Speech/Studio providers; never alter production defaults."""
    from ArmoredSpeech.contracts import AudioClass, SpeechDecision

    class SpeechProcessor:
        intelligence = SimpleNamespace(preflight=lambda: None)

        def analyze(self, _video_path):
            return SpeechDecision(
                audio_class=AudioClass.MUSIC_ONLY,
                language=None,
                speech_detected=False,
                music_detected=True,
                confidence=0.99,
                rationale="isolated lifecycle fixture",
            )

        def prepare_wav(self, _video_path, wav_path, _decision):
            wav_path = Path(wav_path)
            wav_path.parent.mkdir(parents=True, exist_ok=True)
            wav_path.write_bytes(b"isolated-speech-wav")

    def studio_batch_runner(*, root, manifest_path, speech_database, report_path, copy_only, storage_root):
        from ArmoredSpeech.studio_batch_runner import load_complete_batch
        from armored_core.integrity import sha256_file
        from armored_core.storage import Storage as LabStorage

        if copy_only:
            raise AssertionError("Lifecycle tests must use the contract-v5 path, not copy-only mode")
        _, items, hashes = load_complete_batch(Path(root), Path(manifest_path))
        storage = LabStorage(
            Path(root),
            storage_root=Path(storage_root) if storage_root is not None else None,
        )
        processed = []
        for item in items:
            identity = (str(item.source_id), str(item.telegram_message_id))
            expected_hash = hashes[identity]
            original = Path(item.original_path).resolve(strict=True)
            rendered = coordinator.pipeline.studio.process(item)
            rendered_path = Path(rendered.result_path).resolve(strict=True)
            destination = storage.result(
                item.content_id,
                item.affiliate_url,
                item.affiliate_name,
                source_id=item.source_id,
            ).resolve()
            if rendered_path != destination:
                if destination.exists():
                    raise FileExistsError(f"test runner refuses to overwrite {destination}")
                destination.write_bytes(rendered_path.read_bytes())
            if not destination.is_file() or destination.stat().st_size <= 0:
                raise RuntimeError(f"test Studio output missing for {identity}")
            if sha256_file(original).lower() != expected_hash.lower():
                raise RuntimeError(f"test Studio changed original for {identity}")
            processed.append({
                "source_id": identity[0],
                "item_id": identity[1],
                "output_path": str(destination),
                "original_sha256_before": expected_hash,
            })
        return {
            "status": "SUCCESS",
            "originals_intact": True,
            "processed": processed,
            "report_path": str(report_path),
        }

    coordinator._speech_processor_override = SpeechProcessor()
    coordinator._speech_studio_batch_runner_override = studio_batch_runner

class VisionStage:
    def identify(self, item):
        return VisionResult(
            f"product-{item.item_id}",
            f"https://example.invalid/affiliate-{item.item_id}",
            ia_context={"productName": f"Product {item.item_id}"},
        )


class FailingFirstIA:
    def __init__(self):
        self.calls = {}

    def generate_caption_with_evidence(self, context):
        item_id = context["productName"].split()[-1]
        self.calls[item_id] = self.calls.get(item_id, 0) + 1
        if item_id == "100" and self.calls[item_id] == 1:
            from ArmoredIA.caption.generator import CaptionGenerationError
            raise CaptionGenerationError("Nenhuma das 10 candidata(s) passou pela Policy")
        from ArmoredIA.caption.generator import CaptionGenerator

        class Provider:
            def generate_candidates(self, _task, _context, _limit):
                return [f"Olha esse charme 🧡\n#item{item_id}"]

        return CaptionGenerator(provider=Provider()).generate_with_evidence(context)

    def generate_caption(self, context):
        return self.generate_caption_with_evidence(context).caption


class ValidCaptionIA:
    def generate_caption_with_evidence(self, context):
        from ArmoredIA.caption.generator import CaptionGenerator

        class Provider:
            def generate_candidates(self, _task, _context, _limit):
                return ["Olha esse charme 🧡\n#casa"]

        return CaptionGenerator(provider=Provider()).generate_with_evidence(context)

    def generate_caption(self, context):
        return self.generate_caption_with_evidence(context).caption


class DeterministicStudio:
    def process(self, item):
        result = item.workspace / f"{item.item_id}_result.mp4"
        result.write_bytes(f"RESULT-{item.item_id}".encode())
        return SimpleNamespace(working_path=None, result_path=result)


class DeterministicPublisher:
    def __init__(self):
        self.published = []
        self.next_message = 900

    def check_publication(self, item):
        return PublicationCheck.ABSENT

    def publish(self, item):
        self.published.append(item.item_id)
        message_id = f"telegram-{self.next_message}"
        self.next_message += 1
        return PublicationResult(True, message_id)


class RebuildableCatchUpSource:
    _historical_limit = None
    historical_materialization_failed = False
    historical_scan_exhausted = False

    def __init__(self, source_file):
        self.source_file = Path(source_file)
        self.index = 0
        self.reset_count = 0
        self.commits = []
        self.historical_complete = False
        self.live_called = False
        self.db = None

    def _message(self, item_id):
        return SimpleNamespace(
            telegram_message_id=str(item_id),
            source_id="local",
            topic_id=7,
            topic_name="test",
            original_url=f"https://example.invalid/product-{item_id}",
            source_path=self.source_file,
        )

    async def fetch_next_async(self):
        sequence = ["100", "101"]
        if self.index < len(sequence):
            message = self._message(sequence[self.index])
            self.index += 1
            return message
        self.historical_scan_exhausted = True
        return None

    def mark_ingested(self, _item_id):
        pass

    def mark_materialization_failed(self):
        self.historical_materialization_failed = True

    def reset_historical_scan(self):
        self.index = 0
        self.reset_count += 1
        self.historical_materialization_failed = False
        self.historical_scan_exhausted = False

    def commit_live_checkpoints(self, checkpoints):
        self.commits.append(dict(checkpoints))
        if self.db is not None:
            for topic_id, message_id in checkpoints.items():
                self.db.set_sync_topic_checkpoint(int(topic_id), "test", int(message_id))

    def complete_historical_sync(self):
        if self.db is not None:
            self.db.complete_historical_sync()
        self.historical_complete = True

    def is_historical_complete(self):
        return self.historical_complete

    async def fetch_live_candidate_async(self):
        self.live_called = True
        return None, {}


class CaptionBatchRecoveryCatchUpTests(unittest.TestCase):
    def test_waiting_vision_does_not_block_catchup_or_live(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source_file = root / "input.mp4"
            source_file.write_bytes(b"ORIGINAL")

            class WaitingFirstVision:
                def identify(self, item):
                    if item.item_id == "100":
                        raise VisionUnresolvedError("produto Shopee não encontrado")
                    return VisionResult(
                        f"product-{item.item_id}",
                        f"https://example.invalid/affiliate-{item.item_id}",
                        ia_context={"productName": f"Product {item.item_id}"},
                    )

            publisher = DeterministicPublisher()
            source = RebuildableCatchUpSource(source_file)
            coordinator = Coordinator.build(
                root,
                SimpleNamespace(
                    vision=WaitingFirstVision(),
                    ia=ValidCaptionIA(),
                    studio=DeterministicStudio(),
                    publisher=publisher,
                    source=source,
                ),
            )
            source.db = coordinator.db
            _install_coordinator_speech_studio_test_seam(coordinator)

            try:
                asyncio.run(coordinator._run_catch_up_with_recovery_async())

                waiting = coordinator.db.get("100")
                published = coordinator.db.get("101")

                self.assertEqual(waiting.state, State.WAITING_VISION)
                self.assertEqual(published.state, State.PUBLISHED)
                self.assertTrue(published.cleanup_completed)
                self.assertEqual(publisher.published, ["101"])
                self.assertEqual(source.commits, [{7: 100}, {7: 101}])
                self.assertTrue(source.historical_complete)
                self.assertTrue(coordinator.db.historical_complete())

                asyncio.run(
                    coordinator._run_forever_async(
                        max_cycles=1,
                        poll_seconds=0,
                    )
                )
                self.assertTrue(source.live_called)
            finally:
                coordinator.close()

    def test_ia_failure_recovery_does_not_rerun_vision_and_reenters_live(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source_file = root / "input.mp4"
            source_file.write_bytes(b"ORIGINAL")

            vision = VisionStage()
            ia = FailingFirstIA()
            publisher = DeterministicPublisher()
            source = RebuildableCatchUpSource(source_file)
            coordinator = Coordinator.build(
                root,
                SimpleNamespace(
                    vision=vision,
                    ia=ia,
                    studio=DeterministicStudio(),
                    publisher=publisher,
                    source=source,
                ),
            )
            source.db = coordinator.db
            _install_coordinator_speech_studio_test_seam(coordinator)

            try:
                asyncio.run(coordinator._run_catch_up_with_recovery_async())

                item_100 = coordinator.db.get("100")
                item_101 = coordinator.db.get("101")
                self.assertEqual(item_100.state, State.PUBLISHED)
                self.assertTrue(item_100.cleanup_completed)
                self.assertEqual(item_101.state, State.PUBLISHED)
                self.assertTrue(item_101.cleanup_completed)
                self.assertEqual(ia.calls["100"], 2)
                self.assertEqual(ia.calls["101"], 1)
                self.assertEqual(publisher.published, ["101", "100"])
                self.assertEqual(source.reset_count, 1)
                self.assertTrue(source.historical_complete)
                self.assertEqual(source.commits, [{7: 100}, {7: 101}])

                asyncio.run(coordinator._run_forever_async(max_cycles=1, poll_seconds=0))
                self.assertTrue(source.live_called)
            finally:
                coordinator.close()


if __name__ == "__main__":
    unittest.main()
