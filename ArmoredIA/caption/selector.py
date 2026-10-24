from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Any

from .policy import (
    CaptionPolicyError,
    hashtag_context_score,
    normalize_caption,
    validate_caption,
)


class CaptionGenerationError(RuntimeError):
    """Gemini/Policy could not produce a usable caption."""

    def __init__(
        self,
        message: str,
        *,
        batch_id: str | None = None,
        evaluations: tuple["CaptionCandidateEvaluation", ...] = (),
        provider_requests: int = 0,
        feedback_rounds: tuple[tuple[str, ...], ...] = (),
        provider_attempts: tuple[dict[str, Any], ...] = (),
    ):
        super().__init__(message)
        self.batch_id = batch_id
        self.evaluations = evaluations
        self.provider_requests = int(provider_requests)
        self.feedback_rounds = feedback_rounds
        self.provider_attempts = provider_attempts


@dataclass(frozen=True)
class CaptionCandidateEvaluation:
    index: int
    caption: str
    policy_valid: bool
    rejection_reason: str | None
    score: float
    selected: bool = False
    raw_caption: str = ""
    normalization_corrections: tuple[str, ...] = ()


@dataclass(frozen=True)
class CaptionSelection:
    caption: str
    index: int
    score: float


def _context_tokens(product_context: dict[str, Any] | None) -> set[str]:
    if not product_context:
        return set()
    ignored = {
        "productName", "itemId", "shopId", "shopName",
        "priceMin", "priceMax", "sales", "ratingStar",
        "brand", "brandName", "model", "modelName",
        "imageUrl",
    }
    tokens: set[str] = set()
    for key, value in product_context.items():
        if key in ignored or value in (None, "", [], {}):
            continue
        folded = unicodedata.normalize("NFKD", str(value).casefold())
        folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
        for token in re.findall(r"[a-z0-9]+", folded):
            if len(token) >= 4:
                tokens.add(token)
    return tokens


def _caption_score(
    caption: str,
    *,
    product_context: dict[str, Any] | None,
) -> float:
    context_tokens = _context_tokens(product_context)
    main = re.sub(r"#[\wÀ-ÿ]+", "", caption, flags=re.UNICODE)
    main_tokens = [
        unicodedata.normalize("NFKD", token.casefold())
        for token in re.findall(r"[A-Za-zÀ-ÿ0-9]+", main)
    ]
    main_tokens = ["".join(ch for ch in token if not unicodedata.combining(ch)) for token in main_tokens]
    hashtag_tokens = [
        token.casefold()
        for token in re.findall(r"#([\wÀ-ÿ]+)", caption, flags=re.UNICODE)
    ]

    def lexical_stem(token: str) -> str:
        # Portuguese adjective/noun gender and number endings commonly differ
        # between a product context and a short caption (prática/prático,
        # organizado/organizada). This affects ranking only; it never rewrites
        # generated text or changes Policy acceptance.
        if len(token) >= 5 and token[-1:] in {"a", "o", "e", "s"}:
            return token[:-1]
        return token

    context_stems = {lexical_stem(token) for token in context_tokens}

    score = 0.0
    matched_main: set[str] = set()
    for token in main_tokens:
        matched = next((value for value in context_tokens if (
            token == value
            or lexical_stem(token) == lexical_stem(value)
            or (
                min(len(token), len(value)) >= 5
                and (token.startswith(value) or value.startswith(token))
            )
        )), None)
        if matched:
            matched_main.add(matched)
    score += 2.0 * len(matched_main)
    # Do not count the same evidence twice when a tag merely repeats a word
    # already used in the main phrase. A distinct contextual tag still adds
    # useful product relevance.
    matched_tags = {
        token for token in hashtag_tokens
        if token in context_tokens and token not in matched_main
    }
    score += 1.0 * len(matched_tags)

    # Prefer a slightly richer 3-word reaction when relevance is otherwise tied.
    if len(main_tokens) == 3:
        score += 0.25

    # Penalize repetition inside the tiny caption surface.
    score -= 0.25 * (len(main_tokens) - len(set(main_tokens)))
    return round(score, 4)


class CaptionSelector:
    def evaluate(
        self,
        candidates: list[str],
        *,
        product_name: str = "",
        product_context: dict[str, Any] | None = None,
    ) -> tuple[CaptionCandidateEvaluation, ...]:
        evaluations: list[CaptionCandidateEvaluation] = []
        for index, candidate in enumerate(candidates[:10], start=1):
            raw = str(candidate or "")
            normalization = normalize_caption(
                raw,
                product_name=product_name,
                product_context=product_context,
            )
            normalized = normalization.caption
            try:
                caption = validate_caption(
                    normalized,
                    product_name=product_name,
                    product_context=product_context,
                )
            except CaptionPolicyError as exc:
                evaluations.append(
                    CaptionCandidateEvaluation(
                        index=index,
                        caption=normalized,
                        policy_valid=False,
                        rejection_reason=str(exc),
                        score=0.0,
                        raw_caption=raw,
                        normalization_corrections=normalization.corrections,
                    )
                )
                continue

            evaluations.append(
                CaptionCandidateEvaluation(
                    index=index,
                    caption=caption,
                    policy_valid=True,
                    rejection_reason=None,
                    score=_caption_score(
                        caption,
                        product_context=product_context,
                    ),
                    raw_caption=raw,
                    normalization_corrections=normalization.corrections,
                )
            )
        return tuple(evaluations)

    def choose(
        self,
        evaluations: tuple[CaptionCandidateEvaluation, ...],
    ) -> tuple[CaptionSelection, tuple[CaptionCandidateEvaluation, ...]]:
        valid = [evaluation for evaluation in evaluations if evaluation.policy_valid]
        if not valid:
            detail = "; ".join(
                f"{evaluation.index}: {evaluation.rejection_reason}"
                for evaluation in evaluations
                if evaluation.rejection_reason
            ) or "nenhuma candidata retornada"
            raise CaptionGenerationError(
                f"Nenhuma das {len(evaluations)} candidata(s) passou pela Policy: {detail}",
            )

        # Deterministic ranking: highest local relevance score, then the
        # earliest model index only as an explicit tie-breaker.
        best = max(valid, key=lambda evaluation: (evaluation.score, -evaluation.index))
        marked = tuple(
            CaptionCandidateEvaluation(
                index=evaluation.index,
                caption=evaluation.caption,
                policy_valid=evaluation.policy_valid,
                rejection_reason=evaluation.rejection_reason,
                score=evaluation.score,
                selected=evaluation.index == best.index,
                raw_caption=evaluation.raw_caption,
                normalization_corrections=evaluation.normalization_corrections,
            )
            for evaluation in evaluations
        )
        return (
            CaptionSelection(
                caption=best.caption,
                index=best.index,
                score=best.score,
            ),
            marked,
        )

    def select(
        self,
        candidates: list[str],
        *,
        product_name: str = "",
        product_context: dict[str, Any] | None = None,
    ) -> CaptionSelection:
        evaluations = self.evaluate(
            candidates,
            product_name=product_name,
            product_context=product_context,
        )
        selection, _ = self.choose(evaluations)
        return selection
