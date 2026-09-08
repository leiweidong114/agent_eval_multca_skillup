from agent_eval.model_config import resolve_model_profile, resolve_config_secret
from agent_eval.database import resolve_database_config, _sanitize


def test_root_dotenv_overrides_legacy_but_process_environment_wins(tmp_path):
    root = tmp_path / 'backend'
    config = root / 'config'
    config.mkdir(parents=True)
    (config / 'models.yaml').write_text('litellm:\n  model: old\n  api_base: http://old.invalid/v1\n')
    (config / 'local.yaml').write_text('secrets:\n  LITELLM_API_KEY: old-secret\n')
    (root / '.env').write_text('LITELLM_API_KEY=backend-secret\n')
    (tmp_path / '.env').write_text('\ufeffexport LITELLM_API_KEY="root-secret"\nLITELLM_MODEL=glm-4.5-air # default\n'
                                 'LITELLM_API_BASE=http://new.invalid/v1\nLITELLM_USERNAME=admin\n'
                                 "LITELLM_PASSWORD='test#password'\nDATABASE_URL=postgresql://reader:dbsecret@db.invalid:5432/test\n", encoding='utf-8')
    profile = resolve_model_profile(root, environ={})
    assert profile.model == 'glm-4.5-air'
    assert profile.api_base == 'http://new.invalid/v1'
    assert profile.environment['LITELLM_API_KEY'] == 'root-secret'
    assert resolve_config_secret(root, 'LITELLM_PASSWORD', environ={}) == 'test#password'
    assert resolve_config_secret(root, 'LITELLM_API_KEY', environ={'LITELLM_API_KEY': 'process'}) == 'process'
    assert resolve_database_config(root, environ={}).password == 'dbsecret'


def test_full_content_keeps_long_messages_but_redacts_credentials():
    value = _sanitize({'messages': 'x'*50000, 'api_key': 'not-public', 'headers': {'Authorization': 'secret'}}, max_chars=None)
    assert len(value['messages']) == 50000
    assert 'not-public' not in str(value)
    assert "'secret'" not in str(value)
    assert 'not-public' not in str(_sanitize({'body': '{"headers":{"x-api-key":"not-public"}}'}, max_chars=None))


def test_database_wait_does_not_stop_on_the_first_success(monkeypatch):
    from pathlib import Path
    from agent_eval import database
    calls = []
    def fetch(*args, **kwargs):
        calls.append(1)
        return [{'request_id': str(i), 'status': 'success', 'total_tokens': 10} for i in range(1 if len(calls) < 4 else 2)]
    monkeypatch.setattr(database, 'fetch_model_interactions', fetch)
    monkeypatch.setattr(database.time, 'sleep', lambda _: None)
    assert len(database.wait_for_model_interactions(Path('.'))) == 2
    assert len(calls) >= 7
