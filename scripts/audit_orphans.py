#!/usr/bin/env python3
"""Conservative orphan/dead-code audit for ArmoredAudioLab. Read-only by default."""
from __future__ import annotations
import argparse, ast, hashlib, json, os, re, shutil
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

SKIP_DIRS={".git",".venv","venv","__pycache__",".pytest_cache",".mypy_cache","node_modules","dist","build",".tox","runtime","models","checkpoints","storage","videos","outputs"}
SKIP_SUFFIXES={".pyc",".pyo",".sqlite",".sqlite3",".db",".mp4",".mov",".mkv",".wav",".mp3",".flac",".png",".jpg",".jpeg",".webp",".zip",".7z",".rar",".onnx",".pt",".pth",".bin"}
ENTRY_NAMES={"__main__.py","main.py","cli.py","service.py","run.py","app.py","coordinator.py","startup.py","manage.py","setup.py"}
DOC_EXTS={".md",".rst",".txt",".yml",".yaml",".toml",".ini",".ps1",".bat",".cmd",".sh",".json",".py",".xml"}
DYNAMIC=("importlib.import_module","__import__(","entry_points(","pkg_resources","runpy.run_module","runpy.run_path","spec_from_file_location","getattr(","subprocess.","Popen(","create_subprocess")
BACKUP=re.compile(r"(?:\.bak|\.backup|\.old|\.orig|\.save|~)$",re.I)
TEST_DIRS={"test","tests","testing"}

def rel(p,root):
    try: return p.resolve().relative_to(root.resolve()).as_posix()
    except (ValueError,OSError): return p.as_posix()

def skipped(p,root):
    r=Path(rel(p,root))
    return any(x in SKIP_DIRS for x in r.parts[:-1]) or p.suffix.lower() in SKIP_SUFFIXES

def collect(root):
    out=[]
    for base,dirs,names in os.walk(root):
        dirs[:]=sorted(d for d in dirs if d not in SKIP_DIRS and not (d.startswith(".") and d!=".github"))
        for name in sorted(names):
            p=Path(base)/name
            if p.is_file() and not skipped(p,root): out.append(p)
    return out

def modname(p,root):
    r=Path(rel(p,root))
    if r.suffix!=".py": return None
    parts=list(r.with_suffix("").parts)
    if parts and parts[-1]=="__init__": parts.pop()
    return ".".join(parts) if parts and all(x.isidentifier() for x in parts) else None

def parse_py(p, root):
    try: source=p.read_text(encoding="utf-8-sig")
    except (OSError,UnicodeError): return set(),[], "unreadable"
    try: tree=ast.parse(source,filename=str(p))
    except SyntaxError as e: return set(),[],f"syntax error line {e.lineno}: {e.msg}"
    imports=set(); strings=[]
    current=modname(p,root)
    parts=current.split(".") if current else []
    package=parts if p.name=="__init__.py" else parts[:-1]
    for n in ast.walk(tree):
        if isinstance(n,ast.Import): imports.update(a.name for a in n.names)
        elif isinstance(n,ast.ImportFrom):
            if n.level:
                base=package[:max(0,len(package)-(n.level-1))]
                source=".".join(base+(n.module.split(".") if n.module else []))
                if source: imports.add(source)
                for alias in n.names:
                    if alias.name != "*":
                        child=".".join(([source] if source else base)+[alias.name])
                        imports.add(child)
            elif n.module:
                imports.add(n.module)
                for alias in n.names:
                    if alias.name != "*": imports.add(n.module+"."+alias.name)
        elif isinstance(n,ast.Constant) and isinstance(n.value,str):
            s=n.value.strip()
            if s and ("/" in s or "\\\\" in s or s.endswith((".py",".json",".yaml",".yml",".toml",".wav",".png"))): strings.append(s)
    return imports,strings,None

def file_refs(p):
    try: text=p.read_text(encoding="utf-8",errors="ignore")
    except OSError: return set()
    return {m.group(1).replace("\\","/").lstrip("./") for m in re.finditer(r"(?<![\w.-])([A-Za-z0-9_./\\-]+\.(?:py|ps1|bat|cmd|sh|toml|ya?ml|json|txt|md|wav|png|exe|dll))(?![\w.-])",text,re.I)}

def digest(p):
    h=hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda:f.read(1024*1024),b""): h.update(b)
    return h.hexdigest()

def audit(root):
    files=collect(root); py=[p for p in files if p.suffix==".py"]
    modules={m:p for p in py if (m:=modname(p,root))}
    reverse=defaultdict(set); imports_by_file={}; errors=[]; dynamic_files=set(); standalone_entrypoints=[]
    for p in py:
        r=rel(p,root); imports,strings,err=parse_py(p,root); imports_by_file[r]=imports
        try:
            source=p.read_text(encoding="utf-8-sig",errors="ignore")
            tree=ast.parse(source,filename=str(p))
            if any(
                isinstance(node,ast.If)
                and isinstance(node.test,ast.Compare)
                and isinstance(node.test.left,ast.Name)
                and node.test.left.id=="__name__"
                and any(isinstance(op,ast.Eq) for op in node.test.ops)
                and any(isinstance(value,ast.Constant) and value.value=="__main__" for value in node.test.comparators)
                for node in ast.walk(tree)
            ):
                standalone_entrypoints.append(r)
        except (OSError,SyntaxError): pass
        if err: errors.append({"path":r,"error":err})
        try: src=p.read_text(encoding="utf-8",errors="ignore")
        except OSError: src=""
        if any(x in src for x in DYNAMIC): dynamic_files.add(r)
        for imp in imports:
            name=imp.lstrip(".")
            while name:
                if name in modules: reverse[name].add(r); break
                if "." not in name: break
                name=name.rsplit(".",1)[0]
    searchable=[p for p in files if p.suffix.lower() in DOC_EXTS]
    refs={rel(p,root):file_refs(p) for p in searchable}
    candidates=[]
    for m,p in sorted(modules.items()):
        r=rel(p,root)
        if p.name in ENTRY_NAMES or p.name=="__init__.py" or any(x.lower() in TEST_DIRS for x in Path(r).parts): continue
        if r.startswith("scripts/") or r in standalone_entrypoints: continue
        path_ref=any(owner!=r and (r in vals or p.name in vals) for owner,vals in refs.items())
        if not reverse.get(m) and not path_ref:
            candidates.append({"path":r,"classification":"UNREFERENCED_REVIEW","dynamic_loading_detected_in_repository":bool(dynamic_files),
              "reason":"No static in-repository importer or literal path reference found. Check dynamic loading, CLI use, registries, workflows and external callers before removal."})
    # Exact duplicate source/config/text files; evidence only, never deletion authorization.
    hashes=defaultdict(list)
    for p in files:
        if p.suffix.lower() in {".py",".ps1",".bat",".cmd",".sh",".toml",".yaml",".yml",".json",".md",".txt"}:
            try:
                if p.stat().st_size == 0: continue
                hashes[digest(p)].append(rel(p,root))
            except OSError: pass
    duplicates=[{"sha256":h,"paths":ps} for h,ps in hashes.items() if len(ps)>1]
    backups=[{"path":rel(p,root),"classification":"BACKUP_ARTIFACT_REVIEW"} for p in files if BACKUP.search(p.name)]
    # Dependency declarations are parsed conservatively. Import/distribution names do not always match.
    declared=set(); declaration_files=[]
    pyproject=root/"pyproject.toml"
    if pyproject.is_file():
        declaration_files.append(rel(pyproject,root))
        try:
            import tomllib
            data=tomllib.loads(pyproject.read_text(encoding="utf-8-sig"))
            project=data.get("project",{})
            for spec in project.get("dependencies",[]) or []:
                m=re.match(r"\s*([A-Za-z0-9_.-]+)",spec)
                if m: declared.add(re.sub(r"[-_.]+","-",m.group(1)).lower())
            for group in (project.get("optional-dependencies",{}) or {}).values():
                for spec in group or []:
                    m=re.match(r"\s*([A-Za-z0-9_.-]+)",spec)
                    if m: declared.add(re.sub(r"[-_.]+","-",m.group(1)).lower())
        except (OSError,ValueError) as e:
            errors.append({"path":rel(pyproject,root),"error":f"dependency metadata parse error: {e}"})
    depfiles=list(root.glob("requirements*.txt"))+list(root.glob("**/requirements*.txt"))
    for p in dict.fromkeys(depfiles):
        if not p.is_file() or any(x in SKIP_DIRS for x in p.parts): continue
        declaration_files.append(rel(p,root))
        for line in p.read_text(encoding="utf-8-sig",errors="ignore").splitlines():
            s=line.split("#",1)[0].strip()
            if not s or s.startswith(("-",".")) or s.startswith(("http://","https://","git+")): continue
            m=re.match(r"([A-Za-z0-9_.-]+)(?:\[[^]]+\])?\s*(?:[<>=!~;].*)?$",s)
            if m: declared.add(re.sub(r"[-_.]+","-",m.group(1)).lower())
    stdlib=set(getattr(__import__("sys"),"stdlib_module_names",()))
    used=set()
    for values in imports_by_file.values():
        for imp in values:
            top=imp.lstrip(".").split(".",1)[0]
            if top and top not in stdlib and top not in modules: used.add(top)
    special={"cv2":"opencv-python-headless","dotenv":"python-dotenv","PIL":"pillow","yaml":"pyyaml","sklearn":"scikit-learn","bs4":"beautifulsoup4","google":"google-generativeai","fitz":"pymupdf","Crypto":"pycryptodome","imageio_ffmpeg":"imageio-ffmpeg","audio_separator":"audio-separator","faster_whisper":"faster-whisper"}
    used_dists={re.sub(r"[-_.]+","-",special.get(x,x)).lower() for x in used}
    # Some optional packages are consumed as executable CLIs, not Python imports.
    # Detect known command-based use separately to avoid reporting these as dead deps.
    runtime_cli_used=set()
    for p in files:
        if p.suffix.lower() not in DOC_EXTS: continue
        try: source=p.read_text(encoding="utf-8-sig",errors="ignore")
        except OSError: continue
        if re.search(r"audio-separator",source,re.I) and re.search(r"shutil\\.which|_resolve_executable|subprocess\\.(?:run|Popen)",source):
            runtime_cli_used.add("audio-separator")
    possibly_unused=[{"distribution":d,"classification":"POSSIBLY_UNUSED_DEPENDENCY","reason":"No matching Python import detected. Check optional/runtime use and declaration scope before removal; never auto-remove."} for d in sorted(declared) if d not in used_dists and d.replace("-","_") not in used and d not in runtime_cli_used]
    return {"generated_at_utc":datetime.now(timezone.utc).isoformat(),"root":str(root),
      "scope":{"files_scanned":len(files),"python_files":len(py),"python_modules":len(modules),"dynamic_loading_files":sorted(dynamic_files),"standalone_entrypoints":sorted(standalone_entrypoints)},
      "python_unreferenced_candidates":candidates,"possible_unused_dependencies":possibly_unused,"exact_duplicate_files":duplicates,
      "backup_artifacts":backups,"python_parse_issues":errors,"dependency_declaration_files":sorted(set(declaration_files)),
      "safety":{"read_only":True,"automatic_source_deletion":False,"limitations":["Static analysis cannot prove absence of dynamic imports, reflection, plugin entrypoints, subprocess calls or external invocation.","Dependency mapping is heuristic; optional runtimes such as RVC/PyTorch/Fairseq need separate runtime-aware review.","Exact duplicate files are evidence for review, not permission to delete."]}}

def markdown(r):
    s=r["scope"]; out=["# ArmoredAudioLab — auditoria conservadora de órfãos","",f"- Gerado UTC: {r['generated_at_utc']}",f"- Raiz: {r['root']}",f"- Arquivos analisados: {s['files_scanned']}",f"- Arquivos Python: {s['python_files']}",f"- Módulos Python: {s['python_modules']}",f"- Arquivos com carregamento dinâmico: {len(s['dynamic_loading_files'])}","","## Segurança","Modo padrão somente leitura. Nada é apagado. Ausência de import estático significa candidato a revisão, nunca prova final de orfandade.","","## Candidatos Python"]
    out += [f"- [{x['classification']}] {x['path']} — {x['reason']}" for x in r["python_unreferenced_candidates"]] or ["- Nenhum candidato estático."]
    out += ["","## Dependências possivelmente não utilizadas","Não remover automaticamente; nomes de pacote/import podem diferir e dependências opcionais podem ser usadas só em runtime."]
    out += [f"- [{x['classification']}] {x['distribution']}" for x in r["possible_unused_dependencies"]] or ["- Nenhuma candidata."]
    out += ["","## Duplicações byte a byte"]
    out += ["- SHA256 "+x["sha256"]+": "+", ".join(x["paths"]) for x in r["exact_duplicate_files"]] or ["- Nenhuma."]
    out += ["","## Backups (preservados)"]+[f"- {x['path']}" for x in r["backup_artifacts"]]
    out += ["","## Erros de parsing"]+[f"- {x['path']}: {x['error']}" for x in r["python_parse_issues"]]
    out += ["","## Limitações"]+["- "+x for x in r["safety"]["limitations"]]
    return "\n".join(out)+"\n"

def quarantine(root,allowlist,report):
    if not allowlist or not allowlist.is_file(): raise SystemExit("--quarantine-approved exige --allowlist existente")
    allowed=[x.strip().replace("\\","/") for x in allowlist.read_text(encoding="utf-8").splitlines() if x.strip() and not x.lstrip().startswith("#")]
    valid={x["path"] for x in report["python_unreferenced_candidates"]}|{x["path"] for x in report["backup_artifacts"]}
    target=root/"storage"/"audit_quarantine"/datetime.now().strftime("%Y%m%d_%H%M%S"); moved=[]
    for name in allowed:
        if name not in valid: raise SystemExit(f"Bloqueado: {name} não está entre os candidatos atuais")
        src=(root/name).resolve()
        if root.resolve() not in src.parents or not src.is_file(): raise SystemExit(f"Caminho inválido: {name}")
        if any(x.lower() in {"credentials","storage","runtime","models","checkpoints","assets"} for x in Path(name).parts): raise SystemExit(f"Caminho protegido: {name}")
        dst=target/name; dst.parent.mkdir(parents=True,exist_ok=True); shutil.move(str(src),str(dst)); moved.append(name)
    return moved

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root",type=Path,default=Path(__file__).resolve().parents[1])
    ap.add_argument("--out-dir",type=Path,default=None)
    ap.add_argument("--quarantine-approved",action="store_true")
    ap.add_argument("--allowlist",type=Path)
    a=ap.parse_args(); root=a.root.resolve()
    if not root.is_dir(): ap.error(f"raiz inválida: {root}")
    report=audit(root); out=(a.out_dir.resolve() if a.out_dir else root/"storage"/"audits"); out.mkdir(parents=True,exist_ok=True)
    stamp=datetime.now().strftime("%Y%m%d_%H%M%S"); jp=out/f"orphan_audit_{stamp}.json"; mp=out/f"orphan_audit_{stamp}.md"
    jp.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8"); mp.write_text(markdown(report),encoding="utf-8")
    print(f"Arquivos: {report['scope']['files_scanned']} | Python: {report['scope']['python_files']} | candidatos: {len(report['python_unreferenced_candidates'])} | deps revisar: {len(report['possible_unused_dependencies'])} | duplicações: {len(report['exact_duplicate_files'])} | backups: {len(report['backup_artifacts'])} | parse errors: {len(report['python_parse_issues'])}")
    print(f"Markdown: {mp}\nJSON: {jp}")
    if a.quarantine_approved:
        moved=quarantine(root,a.allowlist,report); print(f"Movidos reversivelmente para quarentena: {len(moved)}")
    return 0
if __name__=="__main__": raise SystemExit(main())
