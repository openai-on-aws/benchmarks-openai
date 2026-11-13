"""Package preinstalled Linux ARM64 dependencies and the shared Runtime source."""
import argparse
import hashlib
import json
import shutil
import stat
import struct
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def build(package, output):
    binary=package/'codex_cli_bin/bin/codex'
    header=binary.read_bytes()[:20]
    if header[:4] != b'\x7fELF' or struct.unpack('<H',header[18:20])[0] != 183:
        raise ValueError('Codex binary must be a Linux ARM64 ELF executable')
    for relative in ('customer_eval','customer_workloads','agent','runtime','intake/skills/eval-intake'):
        shutil.copytree(ROOT/relative,package/relative,dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('__pycache__','.pytest_cache'))
    for filename in ('hillclimb.py','optimizer.py','requirements-integration.txt'):
        shutil.copy2(ROOT/filename,package/filename)
    (package/'main.py').write_text(
        'import os\nfrom pathlib import Path\n'
        'os.environ["PYTHONPATH"] = str(Path(__file__).resolve().parent)\n'
        'from runtime.app import app\n'
        'if __name__ == "__main__":\n    app.run()\n')
    files=[p for p in sorted(package.rglob('*')) if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc']
    uncompressed=sum(p.stat().st_size for p in files)
    if uncompressed > 750*1024*1024:raise ValueError('Uncompressed package exceeds AgentCore limit')
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for path in files:
            executable=(path.stat().st_mode & stat.S_IXUSR) or 'codex_cli_bin/bin' in str(path) or 'codex_cli_bin/codex-path' in str(path)
            path.chmod(0o755 if executable else 0o644)
            archive.write(path,path.relative_to(package))
    if output.stat().st_size > 250*1024*1024:raise ValueError('ZIP exceeds AgentCore limit')
    receipt={'archive':str(output),'sha256':hashlib.sha256(output.read_bytes()).hexdigest(),
             'compressed_bytes':output.stat().st_size,'uncompressed_bytes':uncompressed,'files':len(files),
             'binary_architecture':'Linux ARM64','runtime':'PYTHON_3_12'}
    output.with_suffix('.manifest.json').write_text(json.dumps(receipt,indent=2)+'\n')
    return receipt


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(build(args.package.resolve(),args.output.resolve()),indent=2))
