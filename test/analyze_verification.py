"""Audit saved runs without repeating inference or changing original evidence."""
import argparse
import json
from pathlib import Path
from verify_system import acceptance_checks

parser = argparse.ArgumentParser(__doc__)
parser.add_argument('directories', nargs='+', type=Path)
parser.add_argument('--output', required=True, type=Path)
args = parser.parse_args()
rows = []
for directory in args.directories:
    for file in directory.glob('*.json'):
        if file.name == 'summary.json':
            continue
        result = json.loads(file.read_text(encoding='utf-8'))
        if not result.get('agent'):
            continue
        checks = acceptance_checks(result, 'evaluation')
        scoring = result.get('scoring') or {}
        weighted = sum(d['score'] * d['weights']['dimension'] for d in scoring.get('dimensions', {}).values() if d['score'] is not None)
        checks['score_math'] = abs(weighted - (scoring.get('overall_score') or 0)) < 0.011
        rows.append({'agent': result['agent'], 'evidence': str(file.resolve()), 'run_id': result.get('run_id'),
                     'checks': checks, 'passed': all(checks.values()), 'scores': result.get('scores'),
                     'tool_calls': result.get('process_metrics', {}).get('tool_calls'),
                     'model_calls': result.get('database_trace', {}).get('model_call_count'),
                     'judge_model': scoring.get('llm_judge', {}).get('model'),
                     'judge_tokens': scoring.get('llm_judge', {}).get('usage', {}).get('total_tokens')})
history_count = len(rows)
rows = list({row['agent']: row for row in rows}.values())
report = {'selection': 'last specified evidence directory wins per agent; original runs preserved',
          'history_run_count': history_count, 'passed': sum(r['passed'] for r in rows), 'total': len(rows), 'runs': rows}
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(report, ensure_ascii=False, indent=2))
raise SystemExit(0 if rows and all(r['passed'] for r in rows) else 1)
