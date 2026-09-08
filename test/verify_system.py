"""Explicit paid live tests; never collected by pytest.

Run: python test/verify_system.py --phase connectivity --model glm-4.5-air
     python test/verify_system.py --phase evaluation --model glm-4.5-air
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import json
from pathlib import Path
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend' / 'src'))
from agent_eval.cli import _check_agent
from agent_eval.database import _sanitize
from agent_eval.runtime import SUPPORTED_AGENTS, default_agent_command
from agent_eval.runtime import SKILL_ROOTS
from agent_eval.runner import run_evaluation


def acceptance_checks(result, phase):
    checks = {'execution': result.get('status') in {'connected', 'completed'},
              'exact_model': result.get('model_verification', {}).get('verified') is True}
    if phase == 'evaluation':
        files = list(Path(result['result_dir']).glob('skill-up/**/with_skill/**/verification.txt')) if result.get('result_dir') else []
        contents = []
        for file in files:
            raw = file.read_bytes()
            contents.append(raw.decode('utf-16' if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig').strip())
        checks.update(judge=result.get('scoring', {}).get('llm_judge', {}).get('status') == 'completed',
                      with_skill=result.get('scores', {}).get('task_score') == 100,
                      negative_control=result.get('scores', {}).get('baseline_score') == 0,
                      tools=result.get('process_metrics', {}).get('tool_calls', 0) > 0,
                      saved_artifact='MULTICA_SKILL_UP_OK' in contents)
    return checks


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--phase', choices=['connectivity', 'evaluation'], required=True)
    parser.add_argument('--model', default='glm-4.5-air')
    parser.add_argument('--agent', action='append')
    parser.add_argument('--workers', type=int, default=2)
    parser.add_argument('--timeout', type=int, default=180)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    agents = args.agent or [a for a in SUPPORTED_AGENTS if shutil.which(default_agent_command(a))]
    if not agents or args.workers < 1:
        parser.error('No installed agents or invalid workers')
    output = args.output or ROOT / '.runtime' / 'verification' / (datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + args.phase)
    output.mkdir(parents=True, exist_ok=True)

    def test(agent):
        started = time.monotonic()
        try:
            if args.phase == 'connectivity':
                result = _check_agent(argparse.Namespace(agent=agent, profile=None, model=args.model,
                     agent_executable=None, timeout=args.timeout, prompt='Reply with exactly HI.', database_verify=True))
            else:
                result = run_evaluation(project_root=ROOT / 'backend', skill_dir=ROOT / 'backend' / 'skills' / 'example-marker',
                     agent=agent, model=args.model,
                     prompt=f'Read {SKILL_ROOTS[agent]}/example-marker/SKILL.md relative to the current workspace using a tool. If it does not exist, reply SKILL_NOT_FOUND and stop; do not search outside the workspace. Otherwise create artifacts/verification.txt containing its evaluation marker using an available file or shell tool, then reply with the marker only.',
                     must_contain=['MULTICA_SKILL_UP_OK'], max_turns=16, timeout_seconds=args.timeout,
                     benchmark=True, iterations=1, task_name='system-verification',
                     collect_database_trace=True, require_model_verification=True, run_llm_judge_enabled=True)
        except Exception as exc:
            result = {'status': 'exception', 'error': f'{type(exc).__name__}: {exc}'}
        result.update(agent=agent, duration_seconds=round(time.monotonic()-started, 2))
        result['acceptance_checks'] = acceptance_checks(result, args.phase)
        safe = _sanitize(result, max_chars=None)
        (output / (agent+'.json')).write_text(json.dumps(safe, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
        print(agent, result['status'], result['duration_seconds'], flush=True)
        return safe

    rows = []
    with ThreadPoolExecutor(max_workers=min(args.workers,len(agents))) as pool:
        for future in as_completed([pool.submit(test,a) for a in agents]):
            rows.append(future.result())
    passed = sum(all(r['acceptance_checks'].values()) for r in rows)
    summary = {'phase':args.phase, 'model':args.model,'passed':passed,'total':len(rows),'agents':rows}
    (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str),encoding='utf-8')
    print(f'{passed}/{len(rows)}; evidence: {output}', flush=True)
    return 0 if passed == len(rows) else 1


if __name__ == '__main__':
    raise SystemExit(main())
