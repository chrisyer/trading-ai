const express = require('express');
const { createProxyMiddleware } = require('http-proxy-middleware');
const path = require('path');

const PORT = process.env.PORT || 3458;
const CRELLA_GOLD = process.env.CRELLA_GOLD_URL || 'http://100.119.161.65:8099';

const app = express();

app.use('/gold', createProxyMiddleware({
  target: CRELLA_GOLD,
  changeOrigin: true,
  pathRewrite: undefined,
  on: {
    proxyReq: (proxyReq, req) => {
      proxyReq.path = req.originalUrl;
    },
  },
}));

app.use(express.static(path.join(__dirname, 'dist')));

app.use((_req, res) => {
  res.sendFile(path.join(__dirname, 'dist', 'index.html'));
});

app.listen(PORT, '0.0.0.0', () => {
  console.log(`Gold Command Center running on http://0.0.0.0:${PORT}`);
  console.log(`Proxying /gold → ${CRELLA_GOLD}`);
});
