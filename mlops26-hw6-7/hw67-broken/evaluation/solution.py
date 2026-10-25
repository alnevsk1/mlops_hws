"""Реализуйте две функции. Контракт и примеры: EVALUATION.md и tests/test_eval.py."""
import json
import math

COUNTS = ('n', 'correct', 'valid', 'errors')
HASHES = ('protocol_sha256', 'golden_sha256', 'baseline_spec_sha256')
MIN_GOLDEN = 50
MIN_SLICES = 3
MIN_BEHAVIORAL = 3


def _no_duplicate_keys(pairs):
    keys = [k for k, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError('повтор ключа в JSON')
    return dict(pairs)


def _no_constants(name):
    raise ValueError(f'{name} — не JSON')


def parse_answer(raw, choices):
    """Метка из строгого ответа {"answer": "<метка из choices>"} или None.

    Без «доброжелательного» парсинга: markdown-блок, лишние поля, повтор ключа,
    NaN/Infinity, не-строка или метка вне choices — невалидный ответ.
    """
    if not isinstance(raw, str):
        return None
    try:
        value = json.loads(raw, object_pairs_hook=_no_duplicate_keys, parse_constant=_no_constants)
    except ValueError:
        return None
    if not isinstance(value, dict) or set(value) != {'answer'}:
        return None
    answer = value['answer']
    return answer if isinstance(answer, str) and answer in choices else None


def _metrics(items):
    n = len(items)
    m = dict(n=n, correct=sum(i['correct'] for i in items), valid=sum(i['valid'] for i in items),
             errors=sum(bool(i['error']) for i in items))
    m.update(accuracy=m['correct'] / n, format_rate=m['valid'] / n)
    return m


def _summary(per_item):
    names = sorted({s for item in per_item for s in item['slices']})
    return _metrics(per_item), {s: _metrics([i for i in per_item if s in i['slices']]) for s in names}


def score(rows, outputs):
    """Верните overall, slices и per_item. Не удаляйте плохие ответы из знаменателя.

    Строгий ответ: JSON с единственным строковым полем answer из choices.
    outputs: [{id, raw, error, latency_seconds}, ...].
    В overall и каждом срезе: n, correct, valid, errors, accuracy, format_rate.
    В per_item: id, raw, parsed, valid, correct, error, slices.
    Несовпадающие/дублирующиеся id и пустой набор должны вызвать ValueError.
    """
    if not rows:
        raise ValueError('Пустой набор вопросов')
    ids = [row['id'] for row in rows]
    by_id = {o['id']: o for o in outputs}
    if len(set(ids)) != len(ids):
        raise ValueError('Дубли id в наборе вопросов')
    if len(by_id) != len(outputs) or set(by_id) != set(ids):
        raise ValueError('id ответов не совпадают с id вопросов: пропуск, лишний ответ или дубль')
    per_item = []
    for row in rows:
        out = by_id[row['id']]
        error = out.get('error') or None
        # Ошибка генерации — ответа нет, даже если raw что-то содержит; строка остаётся в знаменателе.
        parsed = None if error else parse_answer(out.get('raw'), row['choices'])
        per_item.append(dict(id=row['id'], raw=out.get('raw'), parsed=parsed, valid=parsed is not None,
                             correct=parsed is not None and parsed == row['expected'], error=error,
                             slices=list(row['slices'])))
    overall, slices = _summary(per_item)
    return dict(overall=overall, slices=slices, per_item=per_item)


def _check_metrics(m, where):
    if not isinstance(m, dict):
        raise ValueError(f'{where}: нет метрик')
    for key in COUNTS:
        if type(m.get(key)) is not int or m[key] < 0:
            raise ValueError(f'{where}: {key} отсутствует или не целое неотрицательное')
    n = m['n']
    if n == 0:
        raise ValueError(f'{where}: пустой набор')
    if not m['correct'] <= m['valid'] <= n or m['errors'] > n:
        raise ValueError(f'{where}: counts противоречат друг другу')
    for key, num in (('accuracy', 'correct'), ('format_rate', 'valid')):
        value = m.get(key)
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError(f'{where}: {key} отсутствует, NaN или inf')
        if not math.isclose(value, m[num] / n, rel_tol=0, abs_tol=1e-12):
            raise ValueError(f'{where}: {key} не равна {num}/n')


def _check_scored(scored, where):
    """Отчёт score() целостен: overall и срезы пересчитываются из per_item."""
    if not isinstance(scored, dict) or not isinstance(scored.get('per_item'), list) or not scored['per_item']:
        raise ValueError(f'{where}: нет per_item')
    for item in scored['per_item']:
        if not isinstance(item, dict) or not isinstance(item.get('id'), str) or not item['id']:
            raise ValueError(f'{where}: строка per_item без id')
        if type(item.get('valid')) is not bool or type(item.get('correct')) is not bool:
            raise ValueError(f'{where}: {item["id"]}: valid/correct не bool')
        if item['correct'] and not item['valid'] or item.get('error') and item['valid']:
            raise ValueError(f'{where}: {item["id"]}: correct без valid или valid при ошибке')
        if not isinstance(item.get('slices'), list) or not all(isinstance(s, str) and s for s in item['slices']):
            raise ValueError(f'{where}: {item["id"]}: нет срезов')
    ids = [item['id'] for item in scored['per_item']]
    if len(ids) != len(set(ids)):
        raise ValueError(f'{where}: дубли id в per_item')
    overall, slices = _summary(scored['per_item'])
    _check_metrics(scored.get('overall'), f'{where}.overall')
    if any(scored['overall'][k] != overall[k] for k in COUNTS):
        raise ValueError(f'{where}: overall не соответствует per_item')
    if not isinstance(scored.get('slices'), dict) or set(scored['slices']) != set(slices):
        raise ValueError(f'{where}: состав срезов не соответствует per_item')
    for name, expected in slices.items():
        _check_metrics(scored['slices'][name], f'{where}.slices.{name}')
        if any(scored['slices'][name][k] != expected[k] for k in COUNTS):
            raise ValueError(f'{where}: срез {name} не соответствует per_item')


def _layout(scored):
    return {item['id']: sorted(item['slices']) for item in scored['per_item']}


def quality_gate(baseline, candidate, critical_slices):
    """Верните {passed: True, ...} или вызовите ValueError с причиной отказа.

    Проверьте совместимость протокола/набора/baseline, метрики и counts,
    все id и срезы, общую accuracy/format_rate, критические срезы и behavior.
    Missing, NaN, пустой набор и ошибки генерации означают отказ.
    Пороги задаются до измерения; учебный допуск регрессии равен нулю.
    """
    # 1. Оба отчёта полные, с конкретной моделью и одним протоколом.
    for role, report in (('baseline', baseline), ('candidate', candidate)):
        if not isinstance(report, dict) or report.get('status') != 'complete':
            raise ValueError(f'{role}: отчёт неполный (status != complete)')
        if not isinstance(report.get('model'), dict) or not report['model']:
            raise ValueError(f'{role}: не указана модель')
        for key in HASHES:
            if not isinstance(report.get(key), str) or not report[key]:
                raise ValueError(f'{role}: нет {key}')
        _check_scored(report, role)
        _check_scored(report.get('behavioral'), f'{role}.behavioral')
    for key in HASHES:
        if baseline[key] != candidate[key]:
            raise ValueError(f'Несовместимые отчёты: различается {key}')

    # 2. Один и тот же набор: id, срезы и их размеры.
    if _layout(baseline) != _layout(candidate):
        raise ValueError('Различаются id вопросов или их срезы')
    if _layout(baseline['behavioral']) != _layout(candidate['behavioral']):
        raise ValueError('Различаются behavioral-кейсы')
    b, c = baseline['overall'], candidate['overall']
    if c['n'] < MIN_GOLDEN:
        raise ValueError(f'Golden: {c["n"]} < {MIN_GOLDEN} примеров')
    if len(candidate['slices']) < MIN_SLICES:
        raise ValueError(f'Golden: срезов {len(candidate["slices"])} < {MIN_SLICES}')

    # 3. Ошибки генерации делают сравнение недействительным, у любой из версий.
    for role, report in (('baseline', baseline), ('candidate', candidate)):
        if report['overall']['errors'] or report['behavioral']['overall']['errors']:
            raise ValueError(f'{role}: есть ошибки генерации')

    # Сравниваем counts на одном n — без погрешности float. Допуск регрессии 0.
    if c['correct'] < b['correct']:
        raise ValueError(f'Регрессия accuracy: {c["correct"]}/{c["n"]} < {b["correct"]}/{b["n"]}')
    if c['valid'] < b['valid']:
        raise ValueError(f'Регрессия format_rate: {c["valid"]}/{c["n"]} < {b["valid"]}/{b["n"]}')
    if not isinstance(critical_slices, list) or not critical_slices:
        raise ValueError('Не заданы критические срезы')
    critical = {}
    for name in critical_slices:
        if name not in candidate['slices'] or name not in baseline['slices']:
            raise ValueError(f'Критический срез {name!r} отсутствует в отчёте')
        bs, cs = baseline['slices'][name], candidate['slices'][name]
        if cs['correct'] < bs['correct']:
            raise ValueError(f'Регрессия критического среза {name}: {cs["correct"]}/{cs["n"]} < {bs["correct"]}/{bs["n"]}')
        critical[name] = dict(n=cs['n'], baseline_correct=bs['correct'], candidate_correct=cs['correct'],
                              baseline_accuracy=bs['accuracy'], candidate_accuracy=cs['accuracy'])

    # 4. Все обязательные behavioral-кейсы кандидата проходят.
    beh = candidate['behavioral']['overall']
    if beh['n'] < MIN_BEHAVIORAL:
        raise ValueError(f'Behavioral: {beh["n"]} < {MIN_BEHAVIORAL} кейсов')
    if beh['correct'] != beh['n']:
        failed = [i['id'] for i in candidate['behavioral']['per_item'] if not i['correct']]
        raise ValueError(f'Behavioral: не пройдены {failed}')

    return dict(passed=True, n=c['n'], protocol_sha256=candidate['protocol_sha256'],
                golden_sha256=candidate['golden_sha256'],
                baseline=dict(correct=b['correct'], valid=b['valid'], accuracy=b['accuracy'], format_rate=b['format_rate']),
                candidate=dict(correct=c['correct'], valid=c['valid'], accuracy=c['accuracy'], format_rate=c['format_rate']),
                delta_accuracy=c['accuracy'] - b['accuracy'], delta_format_rate=c['format_rate'] - b['format_rate'],
                critical_slices=critical, behavioral=dict(n=beh['n'], correct=beh['correct']),
                rule='accuracy, format_rate и критические срезы не ниже baseline; все behavioral; 0 ошибок')
