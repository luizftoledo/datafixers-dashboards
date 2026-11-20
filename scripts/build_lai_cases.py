"""Build an index from the CGU's public BuscaLAI export, without copying request text."""
import argparse
import csv
import datetime
import gzip
import io
import json
import re
import zipfile
from pathlib import Path

VERSION = 3
ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'lai/data/public-cases.json'
START_YEAR = 2025
RECENT_LIMIT = 300
IDENTIFIERS = re.compile(
    r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}"
    r"|\b\d{3}[. ]?\d{3}[. ]?\d{3}[- ]?\d{2}\b"
    r"|(?:\+55[ -]*)?\(\d{2}\)[ -]*\d{4,5}[- ]?\d{4}"
    r"|\b\d{2}[- ]\d{4,5}[- ]\d{4}\b"
    r"|\b\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}\b"
)

def excerpt(value, limit):
    cleaned = ' '.join((value or '').split())
    cleaned = IDENTIFIERS.sub('[dado de contato ou identificação omitido]', cleaned)
    return cleaned if len(cleaned) <= limit else cleaned[:limit].rstrip() + ' […]'


def read_public_zip(path):
    records = []
    texts = {}
    recent = []
    with zipfile.ZipFile(path) as archive:
        member, = (name for name in archive.namelist() if '_Pedidos_csv_' in name and name.endswith('.csv'))
        if not any('_PedidosLinkArquivo_csv_' in name for name in archive.namelist()):
            raise ValueError('Use a exportação filtrada do BuscaLAI.')
        match = re.search(r'(20\d{6})_Pedidos_csv_', member)
        if not match:
            raise ValueError(f'Exportação sem data no nome: {member}')
        reference = datetime.datetime.strptime(match[1], '%Y%m%d').date()
        cutoff = reference.replace(day=1) - datetime.timedelta(days=1)
        with archive.open(member) as source:
            lines = (line.replace(chr(0), '') for line in io.TextIOWrapper(source, encoding='utf-16', newline=''))
            for row in csv.DictReader(lines, delimiter=';'):
                decision = row['Decisao'].strip()
                response = row['DataResposta'].strip()
                if row['Esfera'].strip() != 'Federal' or not response or decision not in {
                    'Acesso Negado', 'Acesso Parcialmente Concedido'
                }:
                    continue
                day, month, year = map(int, response.split('/'))
                date = datetime.date(year, month, day)
                if date.year < START_YEAR or date > cutoff:
                    continue
                detail = row.get('DetalhamentoDecisao', '').casefold()
                reason = row.get('EspecificacaoDecisao', '').casefold()
                categories = []
                if 'sigilo profissional do advogado' in detail:
                    categories.append('advogado')
                if 'processo decisório em curso' in reason:
                    categories.append('preparatorio')
                if 'classificada conforme artigos 23 e 24' in reason:
                    categories.append('classificada')
                if 'dados pessoais' in reason:
                    categories.append('informacao-pessoal')
                    if row.get('PrazoRestricaoAcesso', '').strip() == '100':
                        categories.append('cem-anos')
                ident = row['IdPedido'].strip()
                nup = row['ProtocoloPedido'].strip()
                if not re.fullmatch(r'[0-9]+', ident) or not re.fullmatch(r'[0-9./-]{12,25}', nup):
                    continue
                record = [date.isoformat(), row['OrgaoDestinatario'].strip(), decision, nup, ident, categories]
                if categories:
                    records.append(record)
                is_recent = decision == 'Acesso Negado' and date.replace(day=1) == cutoff.replace(day=1)
                # Cases concerning personal data link to the official version only.
                if 'dados pessoais' in reason:
                    text = ['', '']
                elif categories or is_recent:
                    request = row.get('DetalhamentoSolicitacao') or row.get('ResumoSolicitacao')
                    text = [excerpt(request, 700), excerpt(row.get('Resposta'), 950)]
                else:
                    continue
                if categories and any(text):
                    texts[ident] = text
                if is_recent:
                    recent.append([record, text])
    registration_year = int(re.search(r'_Pedidos_csv_(\d{4})\.csv$', member)[1])
    recent.sort(key=lambda r: (r[0][0], int(r[0][4])), reverse=True)
    return {'version': VERSION, 'reference': reference.isoformat(),
            'registration_year': registration_year, 'rows': records,
            'texts': texts, 'recent': recent[:RECENT_LIMIT]}

def write_cases(payloads, output=OUTPUT):
    records = {row[4]: row for payload in payloads for row in payload['rows']}
    years = sorted({p['registration_year'] for p in payloads})
    latest = max(p['reference'] for p in payloads)
    content = {'reference': latest, 'registration_years': years,
               'rows': sorted(records.values(), key=lambda row: (row[0], row[4]), reverse=True)}
    output.write_text(json.dumps(content, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8')
    texts = {ident: text for payload in payloads for ident, text in payload['texts'].items()}
    recent = {row[4]: (row, text) for payload in payloads for row, text in payload['recent']}
    recent = sorted(recent.values(), key=lambda r: (r[0][0], int(r[0][4])), reverse=True)
    newest_month = recent[0][0][0][:7] if recent else ''
    recent = [(row, text) for row, text in recent if row[0].startswith(newest_month)][:RECENT_LIMIT]
    details = {'texts': texts, 'recent': recent}
    encoded = json.dumps(details, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    output.with_name('case-texts.json.gz').write_bytes(gzip.compress(encoded, mtime=0))
    print(f'[casos LAI] {len(records)} pedidos públicos, arquivos {years}, extração {latest}')

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('archives', nargs='+', type=Path)
    args = parser.parse_args()
    write_cases([read_public_zip(path) for path in args.archives])
