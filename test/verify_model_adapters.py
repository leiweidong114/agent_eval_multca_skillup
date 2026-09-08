"""Offline model-ID projection audit. Does not claim inference/tool compatibility."""
import json
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'backend' / 'src'))
from agent_eval.model_config import resolve_model_profile

catalog = json.loads((root / 'backend/config/litellm-models.json').read_text(encoding='utf-8'))
rows = []
for item in catalog['models'] + catalog.get('unavailable_models', []):
    for agent in ['claude', 'codebuddy', 'codex', 'justdo', 'openclaw', 'opencode']:
        runtime = 'openclaw' if agent == 'justdo' else agent
        try:
            profile = resolve_model_profile(root / 'backend', model_override=item['id'], agent=runtime)
            rows.append({'agent': agent, 'model': item['id'], 'projected_model': profile.model_for_agent(runtime),
                         'exact_gateway': profile.gateway_model_for_agent(runtime) == item['id']})
        except ValueError as exc:
            rows.append({'agent': agent, 'model': item['id'], 'exact_gateway': False,
                         'policy_excluded': 'no-thinking' in item['id'].lower(), 'error': str(exc)})
report = {'scope': 'configuration_only_no_inference', 'passed': sum(r['exact_gateway'] for r in rows),
          'policy_excluded': sum(r.get('policy_excluded', False) for r in rows), 'total': len(rows), 'rows': rows}
output = root / '.runtime/verification/model-adapter-resolution.json'
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({k: v for k,v in report.items() if k != 'rows'}))
raise SystemExit(0 if all(r['exact_gateway'] or r.get('policy_excluded') for r in rows) else 1)
