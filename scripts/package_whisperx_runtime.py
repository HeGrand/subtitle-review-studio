"""Stage the verified local WhisperX stack separately from the base ASR dependencies."""
import importlib.metadata
import json
import shutil
from collections import deque
from pathlib import Path


def stage_whisperx(root, checkpoint, license_file):
    from packaging.requirements import Requirement

    checkpoint = Path(checkpoint)
    if checkpoint.name != 'wav2vec2_fairseq_base_ls960_asr_ls960.pth' or not checkpoint.is_file():
        raise ValueError('Expected the English WAV2VEC2_ASR_BASE_960H checkpoint')
    if importlib.metadata.version('whisperx') != '3.2.0' or importlib.metadata.version('faster-whisper') != '1.2.1':
        raise ValueError('Expected the tested WhisperX 3.2.0 and faster-whisper 1.2.1 stack')
    target = Path(root) / '.runtime/whisperx'
    deps = target / 'deps'
    deps.mkdir(parents=True, exist_ok=False)
    # Use only distribution-owned files and keep their package notices.
    queue = deque(('whisperx',))
    packages = {}
    while queue:
        name = queue.popleft().lower().replace('_', '-')
        if name in packages:
            continue
        dist = importlib.metadata.distribution(name)
        packages[name] = dist.version
        for raw in dist.requires or ():
            requirement = Requirement(raw)
            if requirement.marker is None or requirement.marker.evaluate({'extra': ''}):
                queue.append(requirement.name)
        for relative in dist.files or ():
            if (relative.is_absolute() or '..' in relative.parts or '__pycache__' in relative.parts
                    or relative.suffix.lower() in ('.pyc', '.pdb', '.lib')
                    or relative.name == 'direct_url.json' or relative.as_posix() == 'whisperx/assets/pytorch_model.bin'):
                continue
            source = dist.locate_file(relative)
            if source.is_file():
                dest = deps / relative
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, dest)

    checkpoints = target / 'checkpoints'
    checkpoints.mkdir()
    shutil.copyfile(checkpoint, checkpoints / checkpoint.name)
    shutil.copyfile(license_file, checkpoints / 'LICENSE-wav2vec2.txt')
    (checkpoints / 'SOURCE.txt').write_text(
        'WAV2VEC2_ASR_BASE_960H\nhttps://docs.pytorch.org/audio/stable/generated/torchaudio.pipelines.WAV2VEC2_ASR_BASE_960H.html\n'
        f'https://download.pytorch.org/torchaudio/models/{checkpoint.name}\n', encoding='utf-8')
    (target / 'components.json').write_text(json.dumps(packages, sort_keys=True, indent=2)+'\n', encoding='utf-8')
    print(f'WhisperX runtime staged: {len(packages)} distributions', flush=True)
