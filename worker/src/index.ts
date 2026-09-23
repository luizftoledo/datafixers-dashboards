interface Env {
  ARQUIVOS: R2Bucket;
  DB: D1Database;
  AI: any;
  SOURCE_URL: string;
  TELEGRAM_BOT_TOKEN?: string;
  TELEGRAM_CHAT_ID?: string;
  NOTION_TOKEN?: string;
}

const NOTION_DBS = {
  bbc: '6c6b86d6-b57d-4978-b9ff-d129f8ec1985',
  cambridge: '3de4bc10-7286-488d-bf9a-db66e65cf3a4',
};

const CORS = {
  'access-control-allow-origin': '*',
  'access-control-allow-methods': 'GET, OPTIONS',
};

function normCpfCnpj(s: string): string {
  // Remove tudo que não é dígito. PRESERVA zeros à esquerda.
  return (s || '').replace(/\D/g, '');
}
function normText(s: string): string {
  // Lowercase + remove acentos/diacríticos (NFD decompose + strip range U+0300–U+036F)
  return (s || '')
    .normalize('NFD').replace(/[̀-ͯ]/g, '')
    .toLowerCase().trim();
}

async function cachedSearch(env: Env, keyParts: unknown[], query: () => Promise<unknown>): Promise<Response> {
  const bytes = new TextEncoder().encode(JSON.stringify(keyParts));
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  const key = 'cache/ibama-search-v1/' + Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, '0')).join('') + '.json';
  const cached = await env.ARQUIVOS.get(key);
  if (cached) {
    const entry = await cached.json() as { at: number; results: unknown };
    if (Date.now() - entry.at < 24 * 60 * 60 * 1000) return Response.json(entry.results, { headers: CORS });
  }
  const results = await query();
  await env.ARQUIVOS.put(key, JSON.stringify({ at: Date.now(), results }));
  return Response.json(results, { headers: CORS });
}

export default {
  async scheduled(event: ScheduledEvent, env: Env, ctx: ExecutionContext) {
    // Cron 06:00 UTC = scrape + check watchlist; cron 11:00 UTC = digest
    const hour = new Date(event.scheduledTime).getUTCHours();
    if (hour === 11) {
      ctx.waitUntil(sendDigest(env));
    } else {
      ctx.waitUntil((async () => {
        await runUpdate(env, 'cron');
        await checkWatchlist(env);
      })());
    }
  },

  async fetch(req: Request, env: Env): Promise<Response> {
    const url = new URL(req.url);
    const path = url.pathname;
    if (req.method === 'OPTIONS') return new Response(null, { headers: CORS });

    try {
      // Atualização pesada ocorre no GitHub Actions: o ZIP oficial é grande
      // demais para ser descompactado com segurança dentro de um Worker.
      // Não exponha gatilhos administrativos publicamente.
      if (path === '/run' || path === '/digest') {
        return Response.json({ erro: 'operação administrativa não exposta' }, { status: 410, headers: CORS });
      }

      if (path === '/status' || path === '/api/stats') {
        const cacheKey = 'cache/ibama-stats-v1.json';
        const cached = await env.ARQUIVOS.get(cacheKey);
        const entry = cached ? await cached.json() as { cached_at: number; snapshot_id: number | null; stats: unknown; last_snapshot: unknown } : null;
        let last;
        try {
          last = await env.DB.prepare(
            'SELECT id, taken_at, sha256, size_bytes FROM snapshots ORDER BY id DESC LIMIT 1'
          ).first();
        } catch (error) {
          if (entry && /free tier daily row read limit/i.test(String(error))) {
            return Response.json({ stats: entry.stats, last_snapshot: entry.last_snapshot }, { headers: CORS });
          }
          throw error;
        }
        if (entry) {
          if (entry.snapshot_id === (last?.id ?? null) && Date.now() - entry.cached_at < 7 * 24 * 60 * 60 * 1000) {
            return Response.json({ stats: entry.stats, last_snapshot: last }, { headers: CORS });
          }
        }
        const stats = await env.DB.prepare(`
          SELECT
            COUNT(*) AS total,
            COUNT(DISTINCT cpf_cnpj_norm) AS infratores_unicos,
            COUNT(DISTINCT uf) AS estados,
            ROUND(SUM(valor)/1000000.0, 0) AS valor_total_milhoes,
            MIN(SUBSTR(data_auto,1,4)) AS ano_min,
            MAX(SUBSTR(data_auto,1,4)) AS ano_max
          FROM autos_infracao WHERE valor IS NOT NULL
        `).first();
        await env.ARQUIVOS.put(cacheKey, JSON.stringify({ cached_at: Date.now(), snapshot_id: last?.id ?? null, stats, last_snapshot: last }));
        return Response.json({ stats, last_snapshot: last }, { headers: CORS });
      }

      if (path === '/api/coverage') {
        try {
          const coverage = await env.DB.prepare('SELECT checked_at, official_valid, loaded_official, missing_official, invalid_source_ids FROM ibama_coverage WHERE id = 1').first();
          return Response.json(coverage || { status: 'pending' }, { headers: CORS });
        } catch (error) {
          if (/no such table/i.test(String(error))) return Response.json({ status: 'pending' }, { headers: CORS });
          throw error;
        }
      }

      if (path === '/api/uf') {
        const r = await env.DB.prepare(`
          SELECT uf, COUNT(*) AS n, ROUND(SUM(valor)/1000000.0, 1) AS valor_milhoes,
                 ROUND(AVG(valor), 0) AS valor_medio
          FROM autos_infracao WHERE valor IS NOT NULL AND uf IS NOT NULL
          GROUP BY uf ORDER BY n DESC
        `).all();
        return Response.json(r.results, { headers: CORS });
      }

      if (path === '/api/por-ano') {
        const r = await env.DB.prepare(`
          SELECT SUBSTR(data_auto,1,4) AS ano, COUNT(*) AS n,
                 ROUND(SUM(valor)/1000000.0, 1) AS valor_milhoes
          FROM autos_infracao
          WHERE data_auto IS NOT NULL AND valor IS NOT NULL
          GROUP BY ano ORDER BY ano DESC
        `).all();
        return Response.json(r.results, { headers: CORS });
      }

      if (path === '/api/por-mes') {
        const r = await env.DB.prepare(`
          SELECT SUBSTR(data_auto,6,2) AS mes, COUNT(*) AS n,
                 ROUND(SUM(valor)/1000000.0, 1) AS valor_milhoes
          FROM autos_infracao
          WHERE data_auto IS NOT NULL AND valor IS NOT NULL
          GROUP BY mes ORDER BY mes
        `).all();
        return Response.json(r.results, { headers: CORS });
      }

      if (path === '/api/biomas') {
        const r = await env.DB.prepare(`
          SELECT biomas, COUNT(*) AS n, ROUND(SUM(valor)/1000000.0, 1) AS valor_milhoes
          FROM autos_infracao
          WHERE biomas IS NOT NULL AND biomas != '' AND valor IS NOT NULL
          GROUP BY biomas ORDER BY n DESC LIMIT 20
        `).all();
        return Response.json(r.results, { headers: CORS });
      }

      if (path === '/api/top-infratores') {
        const limit = Math.min(100, parseInt(url.searchParams.get('limit') || '20'));
        const ano = url.searchParams.get('ano');
        const orderParam = url.searchParams.get('order'); // valor|n
        const onlyDesmat = url.searchParams.get('desmatamento') === '1';
        const orderBy = orderParam === 'n' ? 'COUNT(*) DESC' : 'SUM(valor) DESC';
        let where = `valor IS NOT NULL AND nome_infrator IS NOT NULL AND nome_infrator != ''`;
        const params: any[] = [];
        if (ano) { where += ` AND SUBSTR(data_auto, 1, 4) = ?`; params.push(ano); }
        if (onlyDesmat) {
          where += ` AND (LOWER(des_infracao) LIKE '%desmat%' OR LOWER(des_infracao) LIKE '%corte%vegeta%' OR LOWER(des_infracao) LIKE '%supress%vegeta%' OR LOWER(des_infracao) LIKE '%floresta%' OR LOWER(tipo_infracao) LIKE '%flora%')`;
        }
        params.push(limit);
        const r = await env.DB.prepare(`
          SELECT nome_infrator, cpf_cnpj, cpf_cnpj_norm, COUNT(*) AS n,
                 ROUND(SUM(valor)/1000000.0, 2) AS valor_milhoes,
                 MIN(data_auto) AS primeiro, MAX(data_auto) AS ultimo
          FROM autos_infracao
          WHERE ${where}
          GROUP BY nome_norm ORDER BY ${orderBy} LIMIT ?
        `).bind(...params).all();
        return Response.json(r.results, { headers: CORS });
      }

      // Recorte de desmatamento — stats + top UF + top infratores + por bioma + por ano
      if (path === '/api/desmatamento') {
        const desmatFilter = `(LOWER(des_infracao) LIKE '%desmat%' OR LOWER(des_infracao) LIKE '%corte%vegeta%' OR LOWER(des_infracao) LIKE '%supress%vegeta%' OR LOWER(des_infracao) LIKE '%floresta%' OR LOWER(tipo_infracao) LIKE '%flora%')`;
        const stats = await env.DB.prepare(`
          SELECT COUNT(*) AS total,
                 ROUND(SUM(valor)/1000000.0, 0) AS valor_total_milhoes,
                 COUNT(DISTINCT cpf_cnpj_norm) AS infratores
          FROM autos_infracao
          WHERE valor IS NOT NULL AND ${desmatFilter}
        `).first();
        const porUf = await env.DB.prepare(`
          SELECT uf, COUNT(*) AS n, ROUND(SUM(valor)/1000000.0, 1) AS valor_milhoes
          FROM autos_infracao
          WHERE valor IS NOT NULL AND ${desmatFilter}
          GROUP BY uf ORDER BY n DESC LIMIT 12
        `).all();
        const topInfratores = await env.DB.prepare(`
          SELECT nome_infrator, cpf_cnpj, COUNT(*) AS n,
                 ROUND(SUM(valor)/1000000.0, 2) AS valor_milhoes
          FROM autos_infracao
          WHERE valor IS NOT NULL AND ${desmatFilter}
            AND nome_infrator IS NOT NULL AND nome_infrator != ''
          GROUP BY nome_norm ORDER BY SUM(valor) DESC LIMIT 15
        `).all();
        const porBioma = await env.DB.prepare(`
          SELECT biomas, COUNT(*) AS n, ROUND(SUM(valor)/1000000.0, 1) AS valor_milhoes
          FROM autos_infracao
          WHERE biomas IS NOT NULL AND biomas != '' AND ${desmatFilter}
          GROUP BY biomas ORDER BY n DESC LIMIT 8
        `).all();
        const porAno = await env.DB.prepare(`
          SELECT SUBSTR(data_auto,1,4) AS ano, COUNT(*) AS n,
                 ROUND(SUM(valor)/1000000.0, 1) AS valor_milhoes
          FROM autos_infracao
          WHERE data_auto IS NOT NULL AND ${desmatFilter}
          GROUP BY ano ORDER BY ano DESC LIMIT 10
        `).all();
        return Response.json({
          stats, por_uf: porUf.results,
          top_infratores: topInfratores.results,
          por_bioma: porBioma.results,
          por_ano: porAno.results
        }, { headers: CORS });
      }

      if (path === '/api/top-municipios') {
        const r = await env.DB.prepare(`
          SELECT municipio, uf, COUNT(*) AS n,
                 ROUND(SUM(valor)/1000000.0, 1) AS valor_milhoes
          FROM autos_infracao
          WHERE municipio IS NOT NULL AND valor IS NOT NULL
          GROUP BY municipio, uf ORDER BY n DESC LIMIT 30
        `).all();
        return Response.json(r.results, { headers: CORS });
      }

      if (path === '/api/top-infracoes') {
        const r = await env.DB.prepare(`
          SELECT des_infracao, tipo_infracao, COUNT(*) AS n,
                 ROUND(SUM(valor)/1000000.0, 1) AS valor_milhoes
          FROM autos_infracao
          WHERE des_infracao IS NOT NULL AND des_infracao != ''
          GROUP BY des_infracao ORDER BY n DESC LIMIT 25
        `).all();
        return Response.json(r.results, { headers: CORS });
      }

      // Busca normalizada: por nome OR cpf/cnpj OR municipio OR descrição da multa
      if (path === '/api/busca') {
        const q = (url.searchParams.get('q') || '').trim();
        if (q.length < 3) return Response.json({ erro: 'q precisa ter 3+ chars' }, { status: 400, headers: CORS });
        const limit = Math.max(1, Math.min(101, parseInt(url.searchParams.get('limit') || '51') || 51));
        const offset = Math.max(0, Math.min(1000000, parseInt(url.searchParams.get('offset') || '0') || 0));
        const scope = url.searchParams.get('scope'); // 'auto' | 'descricao' | default (nome, CPF/CNPJ ou município)
        const nq = normText(q);
        const cq = normCpfCnpj(q);
        const isNumericOnly = cq.length >= 3 && /^[\d.\-\/\s]+$/.test(q);

        // Helper accent-strip SQL — SQLite LIKE não é accent-insensitive
        // (a query 'nq' já está sem acento via normText, normaliza o campo também)
        const stripAcc = (col: string) =>
          `LOWER(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(${col},'á','a'),'à','a'),'ã','a'),'â','a'),'é','e'),'ê','e'),'í','i'),'ó','o'),'ô','o'),'õ','o'),'ú','u'),'ç','c'))`;

        // Busca só em descrição (des_infracao + tipo_infracao + unidade_conservacao + des_local)
        if (scope === 'descricao') {
          const sql = `SELECT * FROM autos_infracao
                       WHERE ${stripAcc('des_infracao')} LIKE ?
                          OR ${stripAcc('tipo_infracao')} LIKE ?
                          OR ${stripAcc('unidade_conservacao')} LIKE ?
                          OR ${stripAcc('des_local')} LIKE ?
                          OR ${stripAcc('biomas')} LIKE ?
                       ORDER BY data_auto DESC, seq_auto DESC LIMIT ? OFFSET ?`;
          return cachedSearch(env, [scope, nq, limit, offset], async () =>
            (await env.DB.prepare(sql).bind('%'+nq+'%', '%'+nq+'%', '%'+nq+'%', '%'+nq+'%', '%'+nq+'%', limit, offset).all()).results);
        }

        if (scope === 'auto') {
          const id = /^\d+$/.test(q) ? Number(q) : -1;
          const sql = `SELECT * FROM autos_infracao
                       WHERE seq_auto = ? OR num_auto = ? OR num_processo = ?
                       ORDER BY data_auto DESC, seq_auto DESC LIMIT ? OFFSET ?`;
          return cachedSearch(env, [scope, q, limit, offset], async () =>
            (await env.DB.prepare(sql).bind(id, q, q, limit, offset).all()).results);
        }

        // Se é só número, busca por CPF/CNPJ (preserva zeros à esquerda).
        // Aceita CNPJ com pontuação (12.345.678/0001-90) pq normCpfCnpj já tirou.
        if (isNumericOnly) {
          const sql = `SELECT * FROM autos_infracao
                       WHERE cpf_cnpj_norm LIKE ? OR cpf_cnpj LIKE ?
                       ORDER BY data_auto DESC, seq_auto DESC LIMIT ? OFFSET ?`;
          return cachedSearch(env, ['numeric', cq, limit, offset], async () =>
            (await env.DB.prepare(sql).bind(cq + '%', '%' + cq + '%', limit, offset).all()).results);
        }

        // Texto: busca em nome + municipio, accent-insensitive nos 2 lados.
        // nome_norm já vem sem acento do ETL, mas LOWER(municipio) ainda tem acentos.
        const sql = `SELECT * FROM autos_infracao
                     WHERE nome_norm LIKE ?
                        OR ${stripAcc('municipio')} LIKE ?
                     ORDER BY data_auto DESC, seq_auto DESC LIMIT ? OFFSET ?`;
        return cachedSearch(env, ['text', nq, limit, offset], async () =>
          (await env.DB.prepare(sql).bind('%' + nq + '%', '%' + nq + '%', limit, offset).all()).results);
      }

      // Busca múltipla de CPF/CNPJ
      if (path === '/api/busca-multi') {
        const raw = (url.searchParams.get('cpfs') || '').trim();
        if (!raw) return Response.json({ erro: 'cpfs= obrigatório (separados por vírgula)' }, { status: 400, headers: CORS });
        const cpfs = raw.split(/[,;\s\n]+/).map(s => normCpfCnpj(s)).filter(s => s.length >= 3).slice(0, 50);
        if (!cpfs.length) return Response.json([], { headers: CORS });
        const placeholders = cpfs.map(() => 'cpf_cnpj_norm LIKE ?').join(' OR ');
        const sql = `SELECT cpf_cnpj_norm, cpf_cnpj, nome_infrator, COUNT(*) AS n,
                            ROUND(SUM(valor)/1000000.0, 2) AS valor_milhoes,
                            MIN(data_auto) AS primeiro, MAX(data_auto) AS ultimo
                     FROM autos_infracao
                     WHERE ${placeholders}
                     GROUP BY cpf_cnpj_norm ORDER BY SUM(valor) DESC`;
        const params = cpfs.map(c => c + '%');
        return cachedSearch(env, ['multi', ...cpfs], async () =>
          (await env.DB.prepare(sql).bind(...params).all()).results);
      }

      // === WATCHLIST ===
      if (path === '/api/watchlist' && req.method === 'GET') {
        const r = await env.DB.prepare(`
          SELECT w.id, w.type, w.value, w.label, w.added_at, w.active,
                 COUNT(h.id) AS total_hits,
                 SUM(CASE WHEN h.notified = 0 THEN 1 ELSE 0 END) AS pending
          FROM watchlist w
          LEFT JOIN watchlist_hits h ON h.watchlist_id = w.id
          GROUP BY w.id ORDER BY w.added_at DESC
        `).all();
        return Response.json(r.results, { headers: CORS });
      }

      if (path === '/api/watchlist' && req.method === 'POST') {
        const body = await req.json() as any;
        const type = body.type === 'nome' ? 'nome' : 'cpf_cnpj';
        const value = String(body.value || '').trim();
        const label = String(body.label || '').trim();
        if (!value) return Response.json({ erro: 'value obrigatório' }, { status: 400, headers: CORS });
        const value_norm = type === 'cpf_cnpj' ? normCpfCnpj(value) : normText(value);
        if (!value_norm) return Response.json({ erro: 'valor inválido' }, { status: 400, headers: CORS });

        try {
          const r = await env.DB.prepare(
            'INSERT INTO watchlist (type, value, value_norm, label) VALUES (?, ?, ?, ?) RETURNING id'
          ).bind(type, value, value_norm, label || null).first();
          return Response.json({ ok: true, id: r?.id, type, value, value_norm, label }, { headers: CORS });
        } catch (e: any) {
          if (String(e).includes('UNIQUE')) {
            return Response.json({ erro: 'já existe' }, { status: 409, headers: CORS });
          }
          throw e;
        }
      }

      if (path.startsWith('/api/watchlist/') && req.method === 'DELETE') {
        const id = parseInt(path.split('/').pop() || '0');
        if (!id) return Response.json({ erro: 'id inválido' }, { status: 400, headers: CORS });
        await env.DB.prepare('DELETE FROM watchlist_hits WHERE watchlist_id = ?').bind(id).run();
        const r = await env.DB.prepare('DELETE FROM watchlist WHERE id = ?').bind(id).run();
        return Response.json({ ok: true, removed: r.meta.changes }, { headers: CORS });
      }

      if (path === '/api/watchlist/check') {
        // checa todos os watchlist items e busca autos novos
        const r = await checkWatchlist(env);
        return Response.json(r, { headers: CORS });
      }

      // Detalhe por CPF/CNPJ específico OU por nome (fallback) — lista todos os autos
      if (path === '/api/infrator') {
        const cpfRaw = (url.searchParams.get('cpf') || '').trim();
        const nome = (url.searchParams.get('nome') || '').trim();
        const cpf = normCpfCnpj(cpfRaw);
        if (!cpf && !nome) return Response.json({ erro: 'cpf= ou nome= obrigatório' }, { status: 400, headers: CORS });

        // Tenta primeiro por CPF normalizado, depois CPF raw, depois nome
        const cols = `seq_auto, num_auto, num_processo, data_auto, data_fato, uf, municipio,
                 nome_infrator, cpf_cnpj, valor, des_infracao, tipo_infracao, des_local,
                 biomas, unidade_conservacao, longitude, latitude, sit_cancelado, ds_sit`;
        let results: any[] = [];

        if (cpf) {
          const r1 = await env.DB.prepare(
            `SELECT ${cols} FROM autos_infracao WHERE cpf_cnpj_norm = ? ORDER BY data_auto DESC LIMIT 1000`
          ).bind(cpf).all();
          results = r1.results as any[];
          if (!results.length) {
            // fallback: cpf cru (alguns têm formatação)
            const r2 = await env.DB.prepare(
              `SELECT ${cols} FROM autos_infracao WHERE cpf_cnpj LIKE ? ORDER BY data_auto DESC LIMIT 1000`
            ).bind('%' + cpf + '%').all();
            results = r2.results as any[];
          }
        }
        if (!results.length && nome) {
          const nq = normText(nome);
          const r3 = await env.DB.prepare(
            `SELECT ${cols} FROM autos_infracao WHERE nome_norm = ? ORDER BY data_auto DESC LIMIT 1000`
          ).bind(nq).all();
          results = r3.results as any[];
        }
        return Response.json(results, { headers: CORS });
      }

      // === BUSCA FULL-TEXT nos relatórios TE ===
      if (path === '/api/te/busca') {
        const q = (url.searchParams.get('q') || '').trim();
        if (q.length < 3) return Response.json({ erro: 'q precisa ter 3+ chars' }, { status: 400, headers: CORS });
        const limit = Math.min(50, parseInt(url.searchParams.get('limit') || '30'));
        const ufFilter = url.searchParams.get('uf');
        const anoFilter = url.searchParams.get('ano');

        // FTS query (multi-palavra com AND)
        const terms = q.split(/\s+/).filter(t => t.length >= 2).map(t => `"${t.replace(/"/g,'""')}"`);
        if (!terms.length) return Response.json([], { headers: CORS });
        const ftsQ = terms.join(' AND ');

        let sql = `
          SELECT
            d.url, d.titulo, d.ano, d.op_num, d.ufs, d.empresa,
            d.cnpj_cpf, d.trabalhadores_resgatados, d.tipo_trabalho,
            d.municipio, d.resumo,
            snippet(te_fts, 4, '<mark>', '</mark>', '…', 35) AS trecho,
            rank AS relevance
          FROM te_fts f
          JOIN te_docs d ON d.rowid = f.rowid
          WHERE te_fts MATCH ?`;
        const params: any[] = [ftsQ];
        if (anoFilter) { sql += ' AND d.ano = ?'; params.push(parseInt(anoFilter)); }
        if (ufFilter) { sql += ' AND d.ufs LIKE ?'; params.push('%' + ufFilter + '%'); }
        sql += ' ORDER BY d.ano DESC, rank LIMIT ?';
        params.push(limit);
        const r = await env.DB.prepare(sql).bind(...params).all();
        return Response.json(r.results, { headers: CORS });
      }

      if (path === '/api/te/doc') {
        const u = url.searchParams.get('url');
        if (!u) return Response.json({ erro: 'url= obrigatório' }, { status: 400, headers: CORS });
        const r = await env.DB.prepare('SELECT * FROM te_docs WHERE url = ?').bind(u).first();
        return Response.json(r || { erro: 'não encontrado' }, { headers: CORS });
      }

      // === OCR de PDF via Llava (vision) ===
      // Aceita: image (base64) OU r2_key (chave do bucket onde a imagem foi pré-upada)
      if (path === '/api/te/ocr' && req.method === 'POST') {
        const body = await req.json() as any;
        const pdfUrl = String(body.url || '').trim();
        const titulo = String(body.titulo || '').trim();
        const ano = body.ano || null;
        const imageBase64 = body.image;
        const r2Key = body.r2_key;
        if (!pdfUrl || (!imageBase64 && !r2Key)) {
          return Response.json({ erro: 'url + (image OU r2_key) obrigatórios' }, { status: 400, headers: CORS });
        }

        // Cache
        const cached = await env.DB.prepare('SELECT resumo, dados, generated_at FROM relatorios_te_resumos WHERE url = ? AND status = ?').bind(pdfUrl, 'ok').first();
        if (cached && !body.force) {
          return Response.json({ cached: true, ...cached, dados: cached.dados ? JSON.parse(cached.dados as string) : null }, { headers: CORS });
        }

        // Pega bytes da imagem: do base64 ou do R2
        let imageBytes: Uint8Array;
        if (r2Key) {
          const obj = await env.ARQUIVOS.get(r2Key);
          if (!obj) return Response.json({ erro: `r2 key not found: ${r2Key}` }, { status: 404, headers: CORS });
          imageBytes = new Uint8Array(await obj.arrayBuffer());
        } else {
          imageBytes = Uint8Array.from(atob(imageBase64), c => c.charCodeAt(0));
        }
        const imageArr = Array.from(imageBytes);

        const prompt = `Este é a primeira página de um relatório de fiscalização do MTE contra trabalho análogo à escravidão.

Extraia e responda APENAS em JSON válido (sem markdown, sem texto antes/depois):
{
  "empresa": "nome da empresa/fazenda/proprietário autuado",
  "cnpj_cpf": "CNPJ ou CPF se aparecer (com pontuação), senão null",
  "municipio": "município(s) onde ocorreu",
  "uf": "sigla UF (2 chars)",
  "data_fatos": "data ou período da operação (string descritiva)",
  "trabalhadores_resgatados": número inteiro (0 se não conseguir identificar),
  "tipo_trabalho": "atividade (carvoaria, pecuária, construção civil, lavoura, garimpo, etc)",
  "resumo": "resumo de 2-3 linhas em português destacando o caso"
}`;

        let aiResp: any;
        try {
          aiResp = await env.AI.run('@cf/llava-hf/llava-1.5-7b-hf', {
            image: imageArr,
            prompt,
            max_tokens: 800,
          });
        } catch (e) {
          return Response.json({ erro: `AI error: ${e}` }, { status: 500, headers: CORS });
        }

        const respText = aiResp?.description || aiResp?.response || JSON.stringify(aiResp);
        let dados: any = null;
        const jsonMatch = respText.match(/\{[\s\S]*\}/);
        if (jsonMatch) { try { dados = JSON.parse(jsonMatch[0]); } catch (e) {} }
        const resumo = (dados && dados.resumo) ? dados.resumo : respText.slice(0, 500);

        await env.DB.prepare(`
          INSERT OR REPLACE INTO relatorios_te_resumos (url, titulo, ano, resumo, dados, model, status, generated_at)
          VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        `).bind(
          pdfUrl, titulo, ano,
          resumo,
          dados ? JSON.stringify(dados) : null,
          '@cf/llava-hf/llava-1.5-7b-hf',
          dados ? 'ok' : 'partial',
          new Date().toISOString()
        ).run();

        return Response.json({ ok: true, dados, resumo, raw: respText.slice(0, 200) }, { headers: CORS });
      }

      // === RELATÓRIOS TRABALHO ESCRAVO — RESUMOS IA (texto) ===
      if (path === '/api/te/resumir' && req.method === 'POST') {
        const body = await req.json() as any;
        const pdfUrl = String(body.url || '').trim();
        const titulo = String(body.titulo || '').trim();
        const ano = body.ano || null;
        if (!pdfUrl) return Response.json({ erro: 'url obrigatório' }, { status: 400, headers: CORS });

        // 1) cache check
        const cached = await env.DB.prepare('SELECT resumo, dados, generated_at FROM relatorios_te_resumos WHERE url = ? AND status = ?').bind(pdfUrl, 'ok').first();
        if (cached && !body.force) {
          return Response.json({ cached: true, ...cached }, { headers: CORS });
        }

        // 2) Texto: aceita do body (caller já extraiu) OU baixa via r.jina.ai
        let text: string;
        if (body.text) {
          text = String(body.text).slice(0, 18000);
        } else {
          const jinaResp = await fetch(`https://r.jina.ai/${pdfUrl}`, {
            headers: { 'X-Return-Format': 'text' }
          });
          if (!jinaResp.ok) {
            return Response.json({ erro: `jina fetch ${jinaResp.status} — passe 'text' no body` }, { status: 502, headers: CORS });
          }
          text = (await jinaResp.text()).slice(0, 18000);
        }

        // 3) chama Llama 3.1 8B
        const prompt = `Você é um analista que lê relatórios de fiscalização do trabalho escravo no Brasil.

Extraia as seguintes informações deste relatório oficial do MTE e responda ESTRITAMENTE em JSON válido:

{
  "empresa": "nome da empresa/fazenda autuada (string ou null)",
  "cnpj_cpf": "CNPJ ou CPF se aparecer formatado, senão null",
  "municipio": "município(s) onde ocorreu (string ou null)",
  "uf": "sigla UF (string 2 chars ou null)",
  "data_fatos": "data da operação no formato YYYY-MM-DD ou string descritiva como 'março de 2018' (string ou null)",
  "trabalhadores_resgatados": número (inteiro, 0 se não conseguir identificar),
  "tipo_trabalho": "tipo da atividade (carvoaria/pecuária/construção civil/agricultura/garimpo/etc, string ou null)",
  "valor_indenizacoes": "valor monetário se citado (string ou null)",
  "resumo": "resumo de 2-3 linhas em português destacando o caso, contexto e severidade"
}

TÍTULO: ${titulo}

TEXTO DO RELATÓRIO:
${text}

JSON:`;

        let aiResp: any;
        try {
          aiResp = await env.AI.run('@cf/meta/llama-3.1-8b-instruct', {
            messages: [{ role: 'user', content: prompt }],
            max_tokens: 800,
          });
        } catch (e) {
          return Response.json({ erro: `AI error: ${e}` }, { status: 500, headers: CORS });
        }

        const respText = aiResp?.response || '';
        // Tenta extrair JSON da resposta (pode vir com markdown)
        let dados: any = null;
        const jsonMatch = respText.match(/\{[\s\S]*\}/);
        if (jsonMatch) {
          try { dados = JSON.parse(jsonMatch[0]); }
          catch (e) { /* ignora */ }
        }
        const resumo = (dados && dados.resumo) ? dados.resumo : respText.slice(0, 500);

        // 4) salva no D1
        await env.DB.prepare(`
          INSERT OR REPLACE INTO relatorios_te_resumos (url, titulo, ano, resumo, dados, model, status, generated_at)
          VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        `).bind(
          pdfUrl, titulo, ano,
          resumo,
          dados ? JSON.stringify(dados) : null,
          '@cf/meta/llama-3.1-8b-instruct',
          dados ? 'ok' : 'partial',
          new Date().toISOString()
        ).run();

        return Response.json({ ok: true, dados, resumo, text_length: text.length }, { headers: CORS });
      }

      if (path === '/api/te/resumos' && req.method === 'GET') {
        const r = await env.DB.prepare(`
          SELECT url, titulo, ano, resumo, dados, status, generated_at
          FROM relatorios_te_resumos
          ORDER BY ano DESC
        `).all();
        const results = (r.results as any[]).map(row => ({
          ...row,
          dados: row.dados ? JSON.parse(row.dados) : null,
        }));
        return Response.json(results, { headers: CORS });
      }

      // === LULOMETRO ===
      if (path === '/api/lul/stats') {
        const stats = await env.DB.prepare(`
          SELECT
            COUNT(*) AS total,
            COUNT(DISTINCT president) AS presidentes,
            COUNT(DISTINCT mandate) AS mandatos,
            MIN(date) AS data_min,
            MAX(date) AS data_max
          FROM lulometro_records
        `).first();
        const byPres = await env.DB.prepare(`
          SELECT president_slug, president, mandate, COUNT(*) AS n
          FROM lulometro_records
          WHERE president IS NOT NULL
          GROUP BY president_slug, president, mandate ORDER BY n DESC
        `).all();
        const byType = await env.DB.prepare(`
          SELECT type, COUNT(*) AS n FROM lulometro_records
          WHERE type IS NOT NULL GROUP BY type ORDER BY n DESC
        `).all();
        const bySource = await env.DB.prepare(`
          SELECT source, COUNT(*) AS n, MAX(date) AS latest_date FROM lulometro_records
          WHERE source IS NOT NULL AND source != '' GROUP BY source ORDER BY n DESC
        `).all();
        return Response.json({
          stats,
          by_president: byPres.results,
          by_type: byType.results,
          by_source: bySource.results,
        }, { headers: CORS });
      }

      if (path === '/api/lul/busca') {
        const q = (url.searchParams.get('q') || '').trim();
        if (q.length < 2) return Response.json({ erro: 'q precisa ter 2+ chars' }, { status: 400, headers: CORS });
        const limit = Math.max(1, Math.min(51, parseInt(url.searchParams.get('limit') || '20') || 20));
        const offset = Math.max(0, Math.min(100000, parseInt(url.searchParams.get('offset') || '0') || 0));
        const president = url.searchParams.get('president');
        const mandate = url.searchParams.get('mandate');
        const source = url.searchParams.get('source');  // planalto | biblioteca | bluesky
        const type = url.searchParams.get('type');      // discurso | entrevista | post

        // FTS5 query — handle special chars
        const ftsQ = q.split(/\s+/).filter(t => t.length >= 2).map(t => `"${t.replace(/"/g,'""')}"`).join(' AND ');
        if (!ftsQ) return Response.json([], { headers: CORS });

        let sql = `
          SELECT r.id, r.date, r.president, r.mandate, r.type, r.source, r.title, r.url,
                 snippet(lulometro_fts, 2, '<mark>', '</mark>', '…', 30) AS excerpt
          FROM lulometro_fts f
          JOIN lulometro_records r ON r.rowid = f.rowid
          WHERE lulometro_fts MATCH ?`;
        const params: any[] = [ftsQ];
        if (president) { sql += ' AND r.president_slug = ?'; params.push(president); }
        if (mandate) { sql += ' AND r.mandate = ?'; params.push(mandate); }
        if (source) { sql += ' AND r.source = ?'; params.push(source); }
        if (type) { sql += ' AND r.type = ?'; params.push(type); }
        sql += ' ORDER BY r.date DESC, r.id DESC LIMIT ? OFFSET ?';
        params.push(limit, offset);
        const r = await env.DB.prepare(sql).bind(...params).all();
        return Response.json(r.results, { headers: CORS });
      }

      if (path === '/api/lul/record') {
        const id = url.searchParams.get('id');
        if (!id) return Response.json({ erro: 'id obrigatório' }, { status: 400, headers: CORS });
        const r = await env.DB.prepare(
          'SELECT * FROM lulometro_records WHERE id = ?'
        ).bind(id).first();
        return Response.json(r || { erro: 'não encontrado' }, { headers: CORS });
      }

      return new Response(JSON.stringify({
        endpoints: {
          ibama: [
            '/api/stats', '/api/uf', '/api/por-ano', '/api/por-mes',
            '/api/biomas', '/api/top-infratores?limit=20',
            '/api/top-municipios', '/api/top-infracoes',
            '/api/busca?q=...', '/api/busca-multi?cpfs=...',
            '/api/infrator?cpf=...',
          ],
          watchlist: [
            'GET /api/watchlist', 'POST /api/watchlist', 'DELETE /api/watchlist/:id',
            'GET /api/watchlist/check',
          ],
          lulometro: [
            '/api/lul/stats', '/api/lul/busca?q=...&president=...&mandate=...',
            '/api/lul/record?id=...',
          ],
          ops: ['/run', '/digest'],
        }
      }, null, 2), { headers: { ...CORS, 'content-type': 'application/json' } });

    } catch (e) {
      return Response.json({ erro: String(e) }, { status: 500, headers: CORS });
    }
  }
};

async function runUpdate(env: Env, trigger: string) {
  const resp = await fetch(env.SOURCE_URL, { method: 'HEAD' });
  if (!resp.ok) {
    console.error(JSON.stringify({ event: 'ibama_source_probe_failed', trigger, status: resp.status }));
    return { ok: false, error: `source probe ${resp.status}` };
  }
  const source = {
    etag: resp.headers.get('etag'),
    last_modified: resp.headers.get('last-modified'),
    content_length: resp.headers.get('content-length'),
  };
  console.log(JSON.stringify({ event: 'ibama_source_probe_ok', trigger, source }));
  return { ok: true, status: 'external_import_scheduled', trigger, source };
}

async function sha256Hex(buf: ArrayBuffer): Promise<string> {
  const h = await crypto.subtle.digest('SHA-256', buf);
  return [...new Uint8Array(h)].map(b => b.toString(16).padStart(2, '0')).join('');
}

// === WATCHLIST ===
async function checkWatchlist(env: Env) {
  // pega todos items ativos
  const items = await env.DB.prepare(
    'SELECT id, type, value_norm, label, value FROM watchlist WHERE active = 1'
  ).all();

  const newHits: any[] = [];
  for (const item of (items.results as any[])) {
    let autos;
    if (item.type === 'cpf_cnpj') {
      autos = await env.DB.prepare(`
        SELECT seq_auto, data_auto, uf, municipio, nome_infrator, valor, des_infracao, cpf_cnpj
        FROM autos_infracao
        WHERE cpf_cnpj_norm LIKE ?
        ORDER BY data_auto DESC LIMIT 50
      `).bind(item.value_norm + '%').all();
    } else {
      autos = await env.DB.prepare(`
        SELECT seq_auto, data_auto, uf, municipio, nome_infrator, valor, des_infracao, cpf_cnpj
        FROM autos_infracao
        WHERE nome_norm LIKE ?
        ORDER BY data_auto DESC LIMIT 50
      `).bind('%' + item.value_norm + '%').all();
    }

    for (const auto of (autos.results as any[])) {
      // tenta inserir hit; se já existe (UNIQUE), pula
      try {
        await env.DB.prepare(
          'INSERT INTO watchlist_hits (watchlist_id, seq_auto, notified) VALUES (?, ?, 0)'
        ).bind(item.id, auto.seq_auto).run();
        newHits.push({
          watchlist_id: item.id,
          label: item.label || item.value,
          ...auto
        });
      } catch (e) {
        // já existia — ignora
      }
    }
  }

  // Manda notificação se há hits novos
  if (newHits.length > 0 && env.TELEGRAM_BOT_TOKEN && env.TELEGRAM_CHAT_ID) {
    const fmtBRL = (v: number) => {
      if (!v) return 'R$ 0';
      if (v >= 1e6) return `R$ ${(v / 1e6).toFixed(1)}M`;
      if (v >= 1e3) return `R$ ${(v / 1e3).toFixed(0)}k`;
      return `R$ ${v.toFixed(0)}`;
    };
    const lines = [`<b>🚨 Watchlist IBAMA · ${newHits.length} alerta(s)</b>`, ``];
    for (const h of newHits.slice(0, 20)) {
      lines.push(`<b>${h.label}</b>`);
      lines.push(`  ${fmtBRL(h.valor)} · ${h.uf || '?'} · ${(h.data_auto || '').slice(0, 10)}`);
      lines.push(`  ${(h.des_infracao || '').slice(0, 80)}`);
      lines.push(``);
    }
    if (newHits.length > 20) lines.push(`... +${newHits.length - 20} mais`);
    lines.push(`→ <a href="https://dashboards.datafixers.org/ibama/">Ver dashboard</a>`);

    await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        chat_id: env.TELEGRAM_CHAT_ID,
        text: lines.join('\n'),
        parse_mode: 'HTML',
        disable_web_page_preview: true,
      }),
    });

    // marca como notificado
    const ids = newHits.map(h => h.watchlist_id);
    if (ids.length) {
      await env.DB.prepare(
        `UPDATE watchlist_hits SET notified = 1 WHERE notified = 0 AND watchlist_id IN (${ids.map(() => '?').join(',')})`
      ).bind(...ids).run();
    }
  }

  return { ok: true, watchlist_items: items.results?.length || 0, new_hits: newHits.length };
}

// === NOTION helpers ===
async function queryNotionDB(token: string, dbId: string): Promise<any[]> {
  const resp = await fetch(`https://api.notion.com/v1/databases/${dbId}/query`, {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${token}`,
      'Notion-Version': '2022-06-28',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      filter: {
        property: 'Status',
        select: { does_not_equal: 'Feito' },
      },
      page_size: 50,
    }),
  });
  if (!resp.ok) return [];
  const data = await resp.json() as any;
  return data.results || [];
}

function getPropTitle(props: any): string {
  for (const k in props) {
    const v = props[k];
    if (v.type === 'title') {
      const t = v.title?.[0]?.plain_text || '';
      return t;
    }
  }
  return '?';
}

function getPropSelect(props: any, name: string): string {
  return props[name]?.select?.name || '';
}

async function fetchNotionTarefas(token: string) {
  const [bbcRaw, camRaw] = await Promise.all([
    queryNotionDB(token, NOTION_DBS.bbc),
    queryNotionDB(token, NOTION_DBS.cambridge),
  ]);

  const parse = (results: any[]) =>
    results.map(p => ({
      title: getPropTitle(p.properties),
      urgencia: getPropSelect(p.properties, 'Urgência'),
      status: getPropSelect(p.properties, 'Status'),
    })).filter(t => t.title && t.title !== '?');

  const bbc = parse(bbcRaw);
  const cambridge = parse(camRaw);

  // Ordena: Hoje > Essa semana > Em breve > resto
  const ordem = { 'Hoje': 0, 'Essa semana': 1, 'Em breve': 2 };
  const sorter = (a: any, b: any) =>
    (ordem[a.urgencia as keyof typeof ordem] ?? 99) - (ordem[b.urgencia as keyof typeof ordem] ?? 99);
  bbc.sort(sorter);
  cambridge.sort(sorter);

  return { bbc, cambridge };
}

// Digest matinal Telegram — resumo IBAMA dos últimos 7 dias
async function sendDigest(env: Env) {
  if (!env.TELEGRAM_BOT_TOKEN || !env.TELEGRAM_CHAT_ID) {
    return { ok: false, error: 'TELEGRAM_BOT_TOKEN ou CHAT_ID não configurados' };
  }

  // Encontra a data mais recente no banco (IBAMA publica com defasagem)
  const latest = await env.DB.prepare(
    `SELECT MAX(data_auto) AS max_date FROM autos_infracao WHERE valor IS NOT NULL`
  ).first();
  const max_date = (latest?.max_date as string || new Date().toISOString().slice(0, 10)).slice(0, 10);
  const sete_dias_antes = new Date(new Date(max_date).getTime() - 7 * 86400000).toISOString().slice(0, 10);

  // Top 5 autos dos últimos 7 dias DO BANCO (não da data atual)
  const top = await env.DB.prepare(`
    SELECT data_auto, uf, municipio, nome_infrator, valor, des_infracao
    FROM autos_infracao
    WHERE data_auto >= ? AND valor IS NOT NULL
    ORDER BY valor DESC LIMIT 5
  `).bind(sete_dias_antes).all();

  // Stats da semana (referente ao banco)
  const week = await env.DB.prepare(`
    SELECT COUNT(*) AS n, ROUND(SUM(valor)/1000000.0, 1) AS total_mi
    FROM autos_infracao WHERE data_auto >= ? AND valor IS NOT NULL
  `).bind(sete_dias_antes).first();

  // Stats do dia (data mais recente)
  const day = await env.DB.prepare(`
    SELECT COUNT(*) AS n, ROUND(SUM(valor)/1000000.0, 1) AS total_mi
    FROM autos_infracao WHERE SUBSTR(data_auto, 1, 10) = ?
  `).bind(max_date).first();

  // Total acumulado
  const totalRow = await env.DB.prepare(
    `SELECT COUNT(*) AS n FROM autos_infracao`
  ).first();

  const fmtBRL = (v: number) => {
    if (!v) return 'R$ 0';
    if (v >= 1e6) return `R$ ${(v / 1e6).toFixed(1)}M`;
    if (v >= 1e3) return `R$ ${(v / 1e3).toFixed(0)}k`;
    return `R$ ${v.toFixed(0)}`;
  };

  const max_dt = max_date.split('-').reverse().join('/');
  const sete_dt = sete_dias_antes.split('-').reverse().join('/');

  const lines: string[] = [
    `<b>☀️ Digest matinal</b>`,
    `<i>${new Date().toLocaleDateString('pt-BR', { weekday: 'long', day: '2-digit', month: 'long' })}</i>`,
  ];

  // === SEÇÃO: NOTION (tarefas) ===
  if (env.NOTION_TOKEN) {
    const tarefas = await fetchNotionTarefas(env.NOTION_TOKEN);
    if (tarefas.bbc.length || tarefas.cambridge.length) {
      lines.push(``, `<b>📋 Suas tarefas</b>`);
      if (tarefas.bbc.length) {
        lines.push(``, `<b>BBC</b>`);
        for (const t of tarefas.bbc.slice(0, 8)) {
          const icon = t.urgencia === 'Hoje' ? '🔥' : '📌';
          lines.push(`${icon} ${t.title}`);
        }
      }
      if (tarefas.cambridge.length) {
        lines.push(``, `<b>Cambridge</b>`);
        for (const t of tarefas.cambridge.slice(0, 5)) {
          const icon = t.urgencia === 'Hoje' ? '🔥' : '📌';
          lines.push(`${icon} ${t.title}`);
        }
      }
    }
  }

  // === SEÇÃO: IBAMA ===
  lines.push(
    ``,
    `<b>🌳 IBAMA · Autos de infração</b>`,
    `📊 Últimos 7d (${sete_dt}→${max_dt}): ${week?.n ?? 0} autos · R$ ${week?.total_mi ?? 0}M`,
    `📦 Banco: ${(totalRow?.n as number ?? 0).toLocaleString('pt-BR')} autos totais`,
    ``,
    `<b>🔥 Top 5 multas recentes</b>`,
  );

  for (const r of (top.results as any[])) {
    const nome = (r.nome_infrator || '?').slice(0, 50);
    const data = (r.data_auto || '').slice(0, 10).split('-').reverse().join('/');
    const inf = (r.des_infracao || '').slice(0, 70);
    lines.push(
      `\n• <b>${fmtBRL(r.valor)}</b> · ${r.uf} · ${data}\n  ${nome}\n  <i>${inf}</i>`
    );
  }

  lines.push(`\n→ <a href="https://dashboards.datafixers.org/ibama/">dashboards.datafixers.org/ibama</a>`);

  const text = lines.join('\n');
  const url = `https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`;
  const resp = await fetch(url, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({
      chat_id: env.TELEGRAM_CHAT_ID,
      text,
      parse_mode: 'HTML',
      disable_web_page_preview: true,
    }),
  });
  const result = await resp.json();
  return { ok: resp.ok, telegram: result, preview: text };
}
