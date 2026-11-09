from __future__ import annotations

import json
import wave

import pytest

from tools.prepare_dataset import DatasetValidationError, main, validate_manifest
from benchmarks.persian_eval import load_set, load_hypotheses


def fixture_manifest(tmp_path, suffix='.json'):
    audio = tmp_path / 'sample.wav'
    with wave.open(str(audio), 'wb') as handle:
        handle.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        handle.writeframes(b'\0\0' * 160)
    manifest = tmp_path / ('manifest' + suffix)
    row = {'id': 'fa-1', 'audio': 'sample.wav', 'text': 'سلام'}
    if suffix == '.csv':
        manifest.write_text('id,audio,text\nfa-1,sample.wav,سلام\n', encoding='utf-8')
    else:
        manifest.write_text(json.dumps([row] if suffix == '.json' else row), encoding='utf-8')
    return manifest, row


@pytest.mark.parametrize('suffix', ['.json', '.jsonl', '.csv'])
def test_formats_and_portable_export(tmp_path, suffix, monkeypatch):
    manifest, _ = fixture_manifest(tmp_path, suffix)
    monkeypatch.chdir(tmp_path.parent)
    assert validate_manifest(manifest)[0]['text'] == 'سلام'
    output = tmp_path / 'export' / 'data.jsonl'
    assert main(['--manifest', str(manifest), '--out', str(output)]) == 0
    assert json.loads(output.read_text())['audio'] == '../sample.wav'
    assert validate_manifest(output)[0]['id'] == 'fa-1'


@pytest.mark.parametrize('change', ['duplicate', 'null', 'missing', 'bad_audio', 'not_object'])
def test_bad_records(tmp_path, change):
    manifest, row = fixture_manifest(tmp_path)
    rows = [row]
    if change == 'duplicate': rows.append(row)
    if change == 'null': row['text'] = None
    if change == 'missing': del row['audio']
    if change == 'bad_audio': (tmp_path / 'sample.wav').write_bytes(b'bad')
    if change == 'not_object': rows = [42]
    manifest.write_text(json.dumps(rows))
    with pytest.raises(DatasetValidationError):
        validate_manifest(manifest)


def test_errors_and_overwrite_protection(tmp_path, capsys):
    manifest, _ = fixture_manifest(tmp_path)
    assert main(['--manifest', str(manifest), '--out', str(manifest)]) == 2
    assert main(['--manifest', str(tmp_path / 'missing.json')]) == 2
    assert 'error:' in capsys.readouterr().err


@pytest.mark.parametrize('row', [None, {'id': '../escape', 'text': 'سلام'},
    {'id': 'x', 'text': 42}, {'id': 'x', 'text': 'speech', 'kind': 'no_speech'}])
def test_eval_schema(tmp_path, row):
    path = tmp_path / 'eval.json'
    path.write_text(json.dumps({'sentences': [row]}))
    with pytest.raises(ValueError): load_set(path)


@pytest.mark.parametrize('data', [[{'id': 'a', 'text': ''}, {'id': 'a', 'text': 'x'}], {'a': None}])
def test_hypothesis_schema(tmp_path, data):
    path = tmp_path / 'hyp.json'
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError): load_hypotheses(path)
