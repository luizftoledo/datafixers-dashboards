"""Build an index from the CGU's public BuscaLAI export, without copying request text."""
import argparse
import csv
import datetime
import io
import json
import re
import zipfile
from pathlib import Path

VERSION = 1
ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'lai/data/public-cases.json'
START_YEAR = 2025

def read_public_zip(path):
    records = []
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
                if 'dados pessoais' in reason and row.get('PrazoRestricaoAcesso', '').strip() == '100':
                    categories.append('cem-anos')
                ident = row['IdPedido'].strip()
                nup = row['ProtocoloPedido'].strip()
                if categories and re.fullmatch(r'[0-9]+', ident) and re.fullmatch(r'[0-9./-]{12,25}', nup):
                    records.append([date.isoformat(), row['OrgaoDestinatario'].strip(),
                                    decision, nup, ident, categories])
    registration_year = int(re.search(r'_Pedidos_csv_(\d{4})\.csv$', member)[1])
    return {'version': VERSION, 'reference': reference.isoformat(),
            'registration_year': registration_year, 'rows': records}

def write_cases(payloads, output=OUTPUT):
    records = {row[4]: row for payload in payloads for row in payload['rows']}
    years = sorted({p['registration_year'] for p in payloads})
    latest = max(p['reference'] for p in payloads)
    content = {'reference': latest, 'registration_years': years,
               'rows': sorted(records.values(), key=lambda row: (row[0], row[4]), reverse=True)}
    output.write_text(json.dumps(content, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8')
    print(f'[casos LAI] {len(records)} pedidos públicos, arquivos {years}, extração {latest}')

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('archives', nargs='+', type=Path)
    args = parser.parse_args()
    write_cases([read_public_zip(path) for path in args.archives])
