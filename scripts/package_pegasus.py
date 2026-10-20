"""Create a portable source-only deployment archive without Windows ACL/mode bits."""
from pathlib import Path
import tarfile


def package(destination):
    roots = ['main.py', 'pyproject.toml', 'requirements.txt', 'src', 'scripts', 'deploy', 'tests',
             'README.md', 'docs/pegasus-prototype-deployment.md']
    def normalize(info):
        if any(part == '__pycache__' or part.endswith('.egg-info') for part in Path(info.name).parts):
            return None
        if not (info.isfile() or info.isdir()):
            raise ValueError('Deployment archive must contain only files and directories')
        info.uid = info.gid = 0
        info.uname = info.gname = ''
        info.mode = 0o755 if info.isdir() else 0o644
        return info
    with tarfile.open(destination, 'w:gz', format=tarfile.PAX_FORMAT) as archive:
        for root in roots:
            archive.add(root, filter=normalize)


if __name__ == '__main__':
    package('data/pegasus/prototype-deploy.tgz')
