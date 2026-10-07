// Cloudflare Pages advanced-mode Worker. No origin identifiers or secrets in source.
const security = {
  'Cache-Control': 'no-store, no-cache, private, max-age=0',
  'Pragma': 'no-cache', 'Expires': '0',
  'Referrer-Policy': 'no-referrer',
  'X-Content-Type-Options': 'nosniff',
  'X-Robots-Tag': 'noindex, nofollow, noarchive',
};
function empty(status) { return new Response(status === 404 ? 'Not Found' : 'Unavailable', {status, headers: security}); }
const home = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>私密分享</title><style>body{font:16px system-ui;max-width:560px;margin:18vh auto;padding:24px;color:#334155}h1{font-size:24px}p{line-height:1.8;color:#64748b}</style><h1>私密分享</h1><p>使用收到的分享链接查看内容。<br>请勿公开转发私人分享链接。</p></html>';
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (!env.PUBLIC_HOST || !['GET','HEAD'].includes(request.method)) return empty(404);
    const head = request.method === 'HEAD';
    // Pages already routes requests to this project; do not reject its production aliases.
    if (url.pathname === '/') return new Response(head ? null : home, {headers:{...security, 'Content-Type':'text/html; charset=utf-8','Content-Security-Policy':"default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'"}});
    const match = /^\/s\/([A-Za-z0-9_-]{22})(\/(?:clash-meta|clash|sing-box|v2ray|v2ray-json))?\/?$/.exec(url.pathname);
    // Clients may append cache-busters or format hints while refreshing.
    // Ignore all query parameters: the upstream remains fixed by the KV record,
    // so even ?url=... cannot turn this into an arbitrary proxy.
    if (!match) return empty(404);
    try {
      const record = await env.SUB_LINKS.get('s:' + match[1], {type:'json'});
      if (!record || typeof record.path !== 'string' || !/^\/sub\/[A-Za-z0-9_.=-]{20,512}$/.test(record.path)) return empty(404);
      if (!env.UPSTREAM_ORIGIN) return empty(503);
      const upstream = new URL(record.path + (match[2] || ''), env.UPSTREAM_ORIGIN);
      if (upstream.origin !== new URL(env.UPSTREAM_ORIGIN).origin) return empty(404);
      const response = await fetch(upstream.href, {
        method:'GET', redirect:'manual', cache:'no-store',
        headers: {'User-Agent':request.headers.get('User-Agent') || 'Shadowrocket/2.2',
          'Accept':'application/octet-stream','Cache-Control':'no-cache'},
      });
      if (response.status === 404 || response.status === 410) { await response.body?.cancel(); return empty(404); }
      if (response.status !== 200) { await response.body?.cancel(); return empty(503); }
      const headers = new Headers(security);
      // Only pass client-format and usage metadata, never backend branding or hyperlinks.
      for (const key of ['Content-Type','Subscription-Userinfo','Profile-Update-Interval','Profile-Title']) {
        const value = response.headers.get(key); if (value) headers.set(key,value);
      }
      const mime = headers.get('Content-Type') || 'application/octet-stream';
      if (mime.toLowerCase().includes('text/html')) { await response.body?.cancel(); return empty(503); }
      headers.set('Content-Type',mime);
      if (head) { await response.body?.cancel(); return new Response(null, {status:200, headers}); }
      return new Response(response.body, {status:200, headers});
    } catch { return empty(503); }
  }
};
