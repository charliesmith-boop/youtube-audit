import fs from "fs";
import crypto from "crypto";
import path from "path";
import { google } from "googleapis";

const storeDir = path.join(process.cwd(), "storage");
const files = {
  tokens: path.join(storeDir, "tokens.json"),
};

export function ensureStores() {
  if (!fs.existsSync(storeDir)) fs.mkdirSync(storeDir, { recursive: true });
  for (const f of Object.values(files)) if (!fs.existsSync(f)) fs.writeFileSync(f, "[]");
}

function read(f){ return JSON.parse(fs.readFileSync(f, "utf8")); }
function write(f, data){ fs.writeFileSync(f, JSON.stringify(data, null, 2)); }

const KEY = Buffer.from(process.env.ENCRYPTION_KEY, "utf8");

function encrypt(text){
  const iv = crypto.randomBytes(12);
  const cipher = crypto.createCipheriv("aes-256-gcm", KEY, iv);
  const enc = Buffer.concat([cipher.update(text, "utf8"), cipher.final()]);
  const tag = cipher.getAuthTag();
  return Buffer.concat([iv, tag, enc]).toString("base64");
}

function decrypt(payload){
  const buf = Buffer.from(payload, "base64");
  const iv = buf.subarray(0,12);
  const tag = buf.subarray(12,28);
  const encd = buf.subarray(28);
  const decipher = crypto.createDecipheriv("aes-256-gcm", KEY, iv);
  decipher.setAuthTag(tag);
  return Buffer.concat([decipher.update(encd), decipher.final()]).toString("utf8");
}

export function getOAuth(){
  return new google.auth.OAuth2(
    process.env.GOOGLE_CLIENT_ID,
    process.env.GOOGLE_CLIENT_SECRET,
    process.env.GOOGLE_REDIRECT_URI
  );
}

export async function saveTokens(channelId, tokens){
  const all = read(files.tokens);
  const row = {
    channelId,
    access_token: tokens.access_token,
    refresh_token: tokens.refresh_token ? encrypt(tokens.refresh_token) : null,
    expiry_date: tokens.expiry_date || null
  };
  const idx = all.findIndex(x => x.channelId === channelId);
  if (idx >= 0) all[idx] = row; else all.push(row);
  write(files.tokens, all);
}

export function loadTokens(channelId){
  const all = read(files.tokens);
  const row = all.find(x => x.channelId === channelId);
  if (!row) return null;
  return {
    access_token: row.access_token,
    refresh_token: row.refresh_token ? decrypt(row.refresh_token) : null,
    expiry_date: row.expiry_date
  };
}

export async function getChannelInfo(oauth){
  const yt = google.youtube("v3");
  const me = await yt.channels.list({ auth: oauth, part: "id,contentDetails", mine: true });
  const ch = me.data.items?.[0];
  return { channelId: ch.id, uploads: ch.contentDetails.relatedPlaylists.uploads };
}

export async function getUploads(oauth, playlist){
  const yt = google.youtube("v3");
  const r = await yt.playlistItems.list({ auth: oauth, part:"contentDetails,snippet", playlistId: playlist, maxResults: 20 });
  return (r.data.items||[]).map(i=>({ videoId:i.contentDetails.videoId, title:i.snippet.title }));
}

export function loadFirstTokens(){
  const all = JSON.parse(fs.readFileSync(files.tokens, "utf8"));
  if (!all.length) return null;
  const row = all[0];
  return {
    channelId: row.channelId || null,
    access_token: row.access_token,
    refresh_token: row.refresh_token ? decrypt(row.refresh_token) : null,
    expiry_date: row.expiry_date || null
  };
}

export async function getRetention(oauth, videoId){
  const ya = google.youtubeAnalytics("v2");
  const res = await ya.reports.query({
    auth: oauth,
    ids: "channel==MINE",
    startDate: "2006-01-01",
    endDate: new Date().toISOString().slice(0,10),
    metrics: "audienceWatchRatio,relativeRetentionPerformance",
    dimensions: "elapsedVideoTimeRatio",
    filters: `video==${videoId}`,
    maxResults: 200
  });

  const rows = res.data.rows || [];
  const series = rows.map(([t, wr])=>({ pos:Number(t), watchRatio:Number(wr)*100 }));
  return series;
}
