// Private connection settings arrive on stdin, never in command-line arguments.
import http from 'node:http';

let input = '';
for await (const chunk of process.stdin) input += chunk;
const settings = JSON.parse(input);
const results = new Array(settings.requests.length);
let next = 0;
async function worker() {
  while (next < settings.requests.length) {
    const index = next++;
    const item = settings.requests[index];
    results[index] = await new Promise(resolve => {
      const data = item.body === undefined ? null : JSON.stringify(item.body);
      const headers = { Authorization: `Bearer ${settings.secret || ''}` };
      if (data) {
        headers['Content-Type'] = 'application/json';
        headers['Content-Length'] = Buffer.byteLength(data);
      }
      const req = http.request({ socketPath: settings.pipe, path: item.path,
        method: item.method || 'GET', headers }, response => {
        let body = '';
        response.setEncoding('utf8');
        response.on('data', chunk => { body += chunk; });
        response.on('end', () => {
          try { body = JSON.parse(body); } catch {}
          resolve({ status: response.statusCode, body });
        });
        response.on('error', error => resolve({ error: error.code || 'RESPONSE_ERROR' }));
      });
      req.on('error', error => resolve({ error: error.code || 'REQUEST_ERROR' }));
      req.setTimeout(settings.timeout_ms || 15000, () => req.destroy());
      req.end(data);
    });
  }
}
await Promise.all(Array.from({ length: Math.min(settings.concurrency || 1, settings.requests.length) }, worker));
process.stdout.write(JSON.stringify(results));
