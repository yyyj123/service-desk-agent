"""Build an allowlisted upload directory, excluding local secrets and runtime."""
import argparse
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    target = args.destination.resolve()
    if target.exists():
        raise SystemExit('Use a new directory; existing builds are never overwritten.')
    target.mkdir(parents=True)
    for name in ('service', 'portal', 'knowledge'):
        shutil.copytree(ROOT / name, target / name,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    # The hosting edge adds browser caching to /static regardless of origin
    # Cache-Control. Content-based filenames keep HTML and assets in sync.
    index_path = target / 'portal' / 'index.html'
    html = index_path.read_text(encoding='utf-8')
    for filename in ('app.js', 'style.css'):
        asset = target / 'portal' / filename
        digest = hashlib.sha256(asset.read_bytes()).hexdigest()[:16]
        versioned = f'{asset.stem}.{digest}{asset.suffix}'
        shutil.copy2(asset, asset.with_name(versioned))
        html = html.replace(f'/static/{filename}', f'/static/{versioned}')
    index_path.write_text(html, encoding='utf-8')
    shutil.copy2(ROOT / 'deploy/cloud_entry.py', target / 'main.py')
    dependencies = [line.strip() for line in (ROOT / 'requirements-enterprise.lock.txt').read_text().splitlines()
                    if line.strip() and not line.startswith('#') and not line.lower().startswith('chromadb==')]
    # Cloud uses PostgreSQL/pgvector exclusively. Do not ship the unused Chroma server.
    (target / 'requirements-cloud.lock.txt').write_text('\n'.join(dependencies)+'\n',encoding='utf-8')
    dependencies.append('fastapi[standard]')
    project = ('[project]\nname = "service-desk-demo"\nversion = "1.0.0"\n'
               'requires-python = "==3.10.*"\ndependencies = ' + json.dumps(dependencies, indent=2) +
               '\n\n[tool.fastapi]\nentrypoint = "main:app"\n'
               '\n[build-system]\nrequires = ["setuptools>=68"]\nbuild-backend = "setuptools.build_meta"\n'
               '\n[tool.setuptools]\npackages = ["service"]\npy-modules = ["main"]\n')
    (target / 'pyproject.toml').write_text(project, encoding='utf-8')
    (target / '.fastapicloudignore').write_text('.env*\nruntime/\n__pycache__/\n*.pyc\n', encoding='utf-8')
    files = [str(p.relative_to(target)) for p in target.rglob('*') if p.is_file()]
    print(json.dumps({'directory':str(target), 'files':len(files)}, ensure_ascii=False))

if __name__ == '__main__':
    main()
