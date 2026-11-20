"""Aggregate recorded LAI reasons by response month and federal agency."""
import argparse
import csv
import io
import json
import re
import unicodedata
import zipfile
from collections import Counter
from pathlib import Path

VERSION = 2
DETAIL_LABELS = {
    'A anonimização ou pseudonimização não assegura a proteção das informações de privacidade, honra e imagem das pessoas',
    'A informação está protegida por propriedade intelectual - Software (Lei no 9.609/1998)',
    'A informação está protegida por segredo de justiça (Lei no 13.105/2015)',
    'A informação está protegida por sigilo bancário (Lei Complementar no 105/2001)',
    'A informação está protegida por sigilo decorrente da proteção à identidade dos denunciantes (Lei no 13.460/2007)',
    'A informação está protegida por sigilo decorrente de direitos autorais (Lei n° 9.610/1998)',
    'A informação está protegida por sigilo do inquérito policial (Decreto-Lei no 3.689/1941)',
    'A informação está protegida por sigilo empresarial ou industrial (Lei no 9.279/1996)',
    'A informação está protegida por sigilo fiscal (Lei no 5.172/1966)',
    'A informação está protegida por sigilo profissional (Decreto-Lei no 2.848/1940)',
    'A informação está protegida por sigilo profissional do advogado (Lei no 8.906/1994)',
    'Causa risco a bens, instalações ou áreas de interesse estratégico',
    'Causa riscos ou prejudica planos ou operações estratégicos das Forças Armadas',
    'Coloca em risco a vida, a segurança ou a saúde da população',
    'Compromete atividades de inteligência, investigação, fiscalização',
    'Corresponde à informação pessoal de privacidade, honra e imagem de terceiro',
    'Desarrazoado',
    'Desproporcional',
    'Gera risco à segurança de instituições ou altas autoridades nacionais ou estrangeiras e seus familiares',
    'Oferece elevado risco à estabilidade financeira, econômica, monetária do país',
    'Parte da informação causa risco a bens, instalações ou áreas de interesse estratégico',
    'Parte da informação causa riscos ou prejudica planos ou operações estratégicos das Forças Armadas',
    'Parte da informação coloca em risco a vida, a segurança ou a saúde da população',
    'Parte da informação compromete atividades de inteligência, investigação, fiscalização',
    'Parte da informação está protegida por propriedade intelectual - Software (Lei no 9.609/1998)',
    'Parte da informação está protegida por segredo de justiça (Lei no 13.105/2015)',
    'Parte da informação está protegida por sigilo bancário (Lei Complementar no 105/2001)',
    'Parte da informação está protegida por sigilo decorrente da proteção à identidade dos denunciantes (Lei no 13.460/2007)',
    'Parte da informação está protegida por sigilo decorrente de direitos autorais (Lei no 9.610/1998)',
    'Parte da informação está protegida por sigilo do inquérito policial (Decreto-Lei no 3.689/1941)',
    'Parte da informação está protegida por sigilo empresarial ou industrial (Lei no 9.279/1996)',
    'Parte da informação está protegida por sigilo fiscal (Lei no 5.172/1966)',
    'Parte da informação está protegida por sigilo profissional (Decreto-Lei no 2.848/1940)',
    'Parte da informação está protegida por sigilo profissional do advogado (Lei no 8.906/1994)',
    'Parte da informação gera risco à segurança de instituições ou altas autoridades nacionais ou estrangeiras e seus familiares',
    'Parte da informação oferece elevado risco à estabilidade financeira, econômica, monetária do país',
    'Parte da informação prejudica negociações ou relações internacionais',
    'Parte da informação prejudica o desenvolvimento científico ou tecnológico',
    'Parte da informação põe em risco a defesa, a soberania ou o território nacional',
    'Parte da informação põe em risco o Presidente, Vice-Presidente ou seus cônjuges e filhos',
    'Prejudica negociações ou relações internacionais',
    'Prejudica o desenvolvimento científico ou tecnológico',
    'Põe em risco a defesa, a soberania ou o território nacional',
    'Põe em risco o Presidente, Vice-Presidente ou seus cônjuges e filhos',
}
DENIED = 'Acesso Negado'
PARTIAL = 'Acesso Parcialmente Concedido'
ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'lai/data/monitor.json'


def normalize(text):
    text = unicodedata.normalize('NFKC', text)
    return re.sub(r'\s+', ' ', text).strip()


def read_zip(path):
    counts = Counter()
    with zipfile.ZipFile(path) as archive:
        member, = (name for name in archive.namelist() if '_Pedidos_csv_' in name and name.endswith('.csv'))
        with archive.open(member) as raw:
            lines = (line.replace('\0', '') for line in io.TextIOWrapper(raw, encoding='utf-16', newline=''))
            for row in csv.DictReader(lines, delimiter=';'):
                date, decision = row['DataResposta'].strip(), row['Decisao'].strip()
                if row['Esfera'].strip() != 'Federal' or not date or not decision:
                    continue
                _, month, year = map(int, date.split('/'))
                org = normalize(row['OrgaoDestinatario'])
                period = year * 100 + month
                counts[period, org, 'respostas', ''] += 1
                if decision in (DENIED, PARTIAL):
                    outcome = 'negado' if decision == DENIED else 'parcial'
                    reason = normalize(row['EspecificacaoDecisao'])
                    counts[period, org, outcome, reason] += 1
                    detail = normalize(row.get('DetalhamentoDecisao') or '')
                    if detail:
                        label = detail if detail in DETAIL_LABELS else 'Outro detalhamento registrado'
                        counts[period, org, f'{outcome}_detalhe', label] += 1
                    term = (row.get('PrazoRestricaoAcesso') or '').strip()
                    if 'dados pessoais' in reason.casefold() and term.isascii() and term.isdecimal() and 1 <= int(term) <= 100:
                        counts[period, org, f'{outcome}_prazo', str(int(term))] += 1
    match = re.search(r'(20\d{6})_Pedidos_csv_', member)
    return {'version': VERSION, 'reference': match[1] if match else '',
            'rows': [[*key, value] for key, value in counts.items()]}


def write_monitor(payloads, output=OUTPUT):
    entries = [record for record in payloads if record.get('version') == VERSION]
    if len(entries) != len(payloads):
        raise ValueError('Faltam arquivos anuais no monitor.')
    counts = Counter()
    for entry in entries:
        for period, org, outcome, reason, count in entry['rows']:
            counts[period, org, outcome, reason] += count
    names = sorted({key[1] for key in counts})
    reasons = sorted({key[3] for key in counts if key[2] != 'respostas'})
    org_indices = {label: i for i, label in enumerate(names)}
    reason_indices = {label: i for i, label in enumerate(reasons)}
    outcomes = {'respostas': 0, 'negado': 1, 'parcial': 2, 'negado_detalhe': 3,
                'parcial_detalhe': 4, 'negado_prazo': 5, 'parcial_prazo': 6}
    response_totals = Counter()
    for (period, org, outcome, _), count in counts.items():
        if outcome == 'respostas':
            response_totals[period] += count
    refs = [entry['reference'] for entry in entries if entry['reference']]
    reference = max(refs, default='')
    last_month = max(response_totals)
    # A label naming the extraction day does not guarantee a complete month.
    cutoff = min(last_month, int(reference[:6]) - 1 if reference and reference[4:6] != '01'
                 else (int(reference[:4]) - 1) * 100 + 12 if reference else last_month)
    records = [[period, org_indices[org], outcomes[outcome], -1 if outcome == 'respostas' else reason_indices[reason], count]
               for (period, org, outcome, reason), count in sorted(counts.items()) if period <= cutoff]
    payload = {'reference': reference, 'cutoff': cutoff, 'orgs': names, 'reasons': reasons, 'rows': records}
    output.write_text(json.dumps(payload, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8')
    print(f'[monitor] {len(records)} linhas agregadas, corte {cutoff}, {len(reasons)} motivos')
    return payload


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('archives', type=Path, nargs='+')
    parser.add_argument('--output', type=Path, default=OUTPUT)
    args = parser.parse_args()
    write_monitor([read_zip(path) for path in args.archives], args.output)
