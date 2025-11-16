// server.js
import 'dotenv/config';
import express from 'express';
import cors from 'cors';
import session from 'cookie-session';
import bodyParser from 'body-parser';
import {
  ensureStores,
  getOAuth,
  saveTokens,
  loadTokens,
  getChannelInfo,
  getUploads,
  getRetention,
  // make sure this exists in retention.js (helper shown below)
  loadFirstTokens
} from './retention.js';

const app = express();
const PORT = 5000;

app.use(cors({ origin: true, credentials: true }));
app.use(bodyParser.json());
app.use(
  session({
    name: 'sess',
    secret: process.env.SESSION_SECRET || 'secret',
    httpOnly: true,
    sameSite: 'lax',
    maxAge: 7 * 24 * 60 * 60 * 1000
  })
);

ensureStores();

/**
 * 1) Get Google consent URL
 */
app.get('/api/oauth2/url', (_req, res) => {
  const oauth = getOAuth();
  const url = oauth.generateAuthUrl({
    access_type: 'offline',
    prompt: 'consent',
    scope: [
      'https://www.googleapis.com/auth/youtube.readonly',
      'https://www.googleapis.com/auth/yt-analytics.readonly'
    ]
  });
  res.json({ url });
});

/**
 * 2) OAuth callback — saves tokens for THIS channel
 */
app.get('/api/oauth2/callback', async (req, res) => {
  try {
    const oauth = getOAuth();
    const { tokens } = await oauth.getToken(req.query.code);
    oauth.setCredentials(tokens);

    const { channelId } = await getChannelInfo(oauth);
    await saveTokens(channelId, tokens);

    res.send('Connected to YouTube. You can close this tab.');
  } catch (e) {
    res.status(500).send('OAuth error: ' + e.message);
  }
});

/**
 * 3) List latest uploads (uses saved tokens FIRST)
 */
app.get('/api/uploads', async (_req, res) => {
  try {
    const oauth = getOAuth();

    // Load the first saved token set (single-tenant dev)
    const first = loadFirstTokens();
    if (!first) return res.status(401).json({ error: 'not_authenticated' });

    oauth.setCredentials(first);

    // Now it’s safe to call channel APIs
    const { uploads } = await getChannelInfo(oauth);
    const vids = await getUploads(oauth, uploads);

    res.json({ uploads: vids });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

/**
 * 4) Retention series + quick insights
 */
app.get('/api/retention/:videoId', async (req, res) => {
  try {
    const oauth = getOAuth();

    const first = loadFirstTokens();
    if (!first) return res.status(401).json({ error: 'not_authenticated' });

    oauth.setCredentials(first);

    const series = await getRetention(oauth, req.params.videoId);

    // Simple insights: mark drops >= 5% between buckets
    const drops = [];
    for (let i = 1; i < series.length; i++) {
      const delta = series[i].watchRatio - series[i - 1].watchRatio;
      if (delta <= -5) {
        drops.push({
          atPercent: Math.round(series[i].pos * 100),
          change: delta.toFixed(1) + '%'
        });
      }
    }

    res.json({ series, insights: { drops } });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

app.listen(PORT, () => {
  console.log('API running http://localhost:' + PORT);
});
