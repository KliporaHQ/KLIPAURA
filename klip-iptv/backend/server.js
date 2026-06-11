const express = require('express');
const { createProxyMiddleware } = require('http-proxy-middleware');
const sqlite3 = require('sqlite3').verbose();
const bodyParser = require('body-parser');
const cors = require('cors');

const app = express();
const PORT = process.env.PORT || 3000;

app.use(cors());
app.use(bodyParser.json());

// Database setup
const db = new sqlite3.Database('./klip_devices.db');

db.run(`CREATE TABLE IF NOT EXISTS devices (
  mac TEXT PRIMARY KEY,
  device_key TEXT,
  playlist_url TEXT,
  xtream_host TEXT,
  xtream_username TEXT,
  xtream_password TEXT,
  enable_proxy INTEGER DEFAULT 1,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
)`);

app.get('/', (req, res) => {
  res.send(`<h1>KLIP IPTV Backend Running \uD83D\uDE80</h1><p>Portal: <a href="/portal">/portal</a></p>`);
});

// Link Playlist
app.post('/api/link', (req, res) => {
  const { mac, device_key, playlist_url, xtream_host, xtream_username, xtream_password, enable_proxy } = req.body;
  if (!mac) return res.status(400).json({ error: 'MAC address is required' });

  db.run(`INSERT OR REPLACE INTO devices 
    (mac, device_key, playlist_url, xtream_host, xtream_username, xtream_password, enable_proxy) 
    VALUES (?, ?, ?, ?, ?, ?, ?)`,
    [mac.toLowerCase(), device_key, playlist_url, xtream_host, xtream_username, xtream_password, enable_proxy ? 1 : 0],
    (err) => {
      if (err) return res.status(500).json({ error: err.message });
      res.json({ success: true, message: 'Device linked successfully!' });
    }
  );
});

// Fetch Playlist
app.get('/api/playlist', (req, res) => {
  const { mac, device_key } = req.query;
  if (!mac) return res.status(400).json({ error: 'MAC required' });

  db.get('SELECT * FROM devices WHERE mac = ? AND (device_key IS NULL OR device_key = ?)', 
    [mac.toLowerCase(), device_key], (err, row) => {
      if (err || !row) return res.status(404).json({ error: 'No playlist found for this device' });
      
      res.json({
        playlist_url: row.playlist_url,
        xtream: row.xtream_host ? {
          host: row.xtream_host,
          username: row.xtream_username,
          password: row.xtream_password
        } : null,
        proxy_enabled: !!row.enable_proxy,
        proxy_base: row.enable_proxy ? `http://${req.headers.host}/proxy` : null
      });
    });
});

// Geo-Proxy
app.use('/proxy', createProxyMiddleware({
  target: '',
  changeOrigin: true,
  pathRewrite: (path, req) => {
    const originalUrl = req.query.url || decodeURIComponent(path.replace('/proxy/', ''));
    return originalUrl.startsWith('http') ? originalUrl.replace(/^https?:\//\//, '') : '';
  },
  router: (req) => {
    try {
      const urlParam = req.query.url || '';
      if (urlParam) return new URL(urlParam).origin;
    } catch (e) {}
    return '';
  },
  onProxyReq: (proxyReq, req) => {
    proxyReq.setHeader('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36');
  },
  onError: (err, req, res) => {
    console.error('Proxy error:', err);
    res.status(502).send('Proxy Error - Stream unavailable');
  }
}));

app.listen(PORT, () => {
  console.log(`\uD83D\uDE80 KLIP IPTV Backend + Geo Proxy running on http://localhost:${PORT}`);
});