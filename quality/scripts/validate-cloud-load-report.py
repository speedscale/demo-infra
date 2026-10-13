#!/usr/bin/env python3
"""Require complete low-data load evidence before accepting a Cloud verdict."""
import json
import sys


def validate(document, config, latency_table=None):
    report = document['report']
    actual = report['actualConfig']
    if report['status'] != 'Passed' or actual.get('id') != config['id']:
        raise ValueError('Cloud report/config does not match the requested load profile')
    if actual['generator'].get('lowDataMode') is not True or actual['responder'].get('lowDataMode') is not True:
        raise ValueError('Cloud load generator and responder must use low data mode')
    if any(goal.get('status') == 'FAIL' for goal in report.get('goals', [])):
        raise ValueError('Cloud load report contains a failed goal')
    if config['generator'].get('stages'):
        if actual['generator'].get('loadGroups') or not actual['generator'].get('stages'):
            raise ValueError('Cloud gRPC load stages are missing or unexpected')
        total = latency_table['total']
        if not isinstance(total.get('count'), int) or total['count'] < 1:
            raise ValueError('Cloud gRPC load has no measured requests')
        for field in ('failedCount', 'noMatchCount', 'errorCount'):
            value = total.get(field, 0)
            if not isinstance(value, (int, float)) or value != 0:
                raise ValueError('Cloud gRPC load has failed or mismatched requests: ' + field)
        ceiling = next(r['value'] for r in config['rules'] if r['metricName'] == 'p95Latency')
        p95 = total.get('p95')
        if not isinstance(p95, (int, float)) or not 0 <= p95 <= ceiling:
            raise ValueError('Cloud gRPC load latency is missing or exceeds its ceiling')
        return
    if actual['generator'].get('stages'):
        raise ValueError('Legacy stages cannot provide the required HTTP error evidence')
    groups = actual['generator'].get('loadGroups', [])
    expected = config['generator']['loadGroups']
    if len(groups) != 1 or groups[0]['id'] != expected[0]['id']:
        raise ValueError('Cloud load group is missing or unexpected')
    group_id = groups[0]['id']
    actual_goals = {g['id']: g for g in groups[0].get('goals', [])}
    for goal in expected[0]['goals']:
        actual_goal = actual_goals[goal['id']]
        rule = actual_goal['rule']
        if (rule.get('metricName') != goal['rule']['metricName']
                or rule.get('value', 0) != goal['rule']['value']
                or rule.get('type', 'TOO_HIGH') != goal['rule']['type']
                or int(actual_goal.get('minSamples', 0)) < 1):
            raise ValueError('Cloud latency goal differs from the requested profile')
    goals = report.get('goals', [])
    for command in ('load_run', 'load_delivery', 'load_requests'):
        matches = [g for g in goals if g.get('command') == command
                   and (command == 'load_run' or g.get('tags', {}).get('load_group_id') == group_id)]
        if len(matches) != 1 or matches[0].get('status') != 'PASS':
            raise ValueError('Cloud load evidence missing or failed: ' + command)
    for goal in expected[0]['goals']:
        matches = [g for g in goals if g.get('tags', {}).get('load_group_id') == group_id
                   and g.get('tags', {}).get('load_goal_id') == goal['id']]
        if len(matches) != 1 or matches[0].get('status') != 'PASS':
            raise ValueError('Cloud latency evidence missing or failed: ' + goal['id'])


if __name__ == '__main__':
    try:
        with open(sys.argv[1]) as source:
            document = json.load(source)
        with open(sys.argv[2]) as source:
            config = json.load(source)
        latency_table = None
        if len(sys.argv) > 3:
            with open(sys.argv[3]) as source:
                latency_table = json.load(source)
        validate(document, config, latency_table)
    except (OSError, ValueError, KeyError, TypeError, IndexError) as error:
        print('Cloud load validation failed: ' + str(error), file=sys.stderr)
        sys.exit(1)
    print('Cloud low-data error, delivery, and latency evidence passed')
