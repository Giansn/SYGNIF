import re

with open('user_data/dashboard_full.html', 'r') as f:
    content = f.read()

# Add caching logic
content = re.sub(
    r"let tk = '';\nlet authFails = 0;\nlet cachedConfig = null;\nlet cachedWhitelist = null;\nlet lastCacheUpdate = 0;",
    r"""let tk = '';
let _authFailCount = 0;
let _cfgCache = null, _cfgCacheTs = 0;
let _wlCache = null, _wlCacheTs = 0;
const CACHE_TTL_MS = 5 * 60 * 1000;

async function getCfg() {
  if (!_cfgCache || Date.now() - _cfgCacheTs > CACHE_TTL_MS) {
    _cfgCache = await api('show_config');
    _cfgCacheTs = Date.now();
  }
  return _cfgCache;
}

async function getWl() {
  if (!_wlCache || Date.now() - _wlCacheTs > CACHE_TTL_MS) {
    _wlCache = await api('whitelist');
    _wlCacheTs = Date.now();
  }
  return _wlCache;
}""",
    content
)

# Fix autoLogin function
content = re.sub(
    r"""async function autoLogin\(\) \{
  const r = await fetch\(`\$\{API\}/api/v1/token/login`, \{
    method: 'POST',
    headers: \{ 'Authorization': 'Basic ' \+ '__SYGNIF_FT_BASIC_B64__' \}
  \}\);
  if \(\!r\.ok\) \{
    authFails\+\+;
    throw new Error\('auth'\);
  \}
  authFails = 0;
  const d = await r\.json\(\);
  tk = d\.access_token;
\}""",
    r"""async function autoLogin() {
  try {
    const r = await fetch(`${API}/api/v1/token/login`, {
      method: 'POST',
      headers: { 'Authorization': 'Basic ' + '__SYGNIF_FT_BASIC_B64__' }
    });
    if (!r.ok) throw new Error('auth');
    const d = await r.json();
    tk = d.access_token;
    _authFailCount = 0;
  } catch (e) {
    _authFailCount++;
    throw e;
  }
}""",
    content
)

# Fix updateApiStatusError and retryAuth
content = re.sub(
    r"""function updateApiStatusError\(msg\) \{
  const el = document\.getElementById\('api-status'\);
  el\.className = 'text-xs font-medium dn';
  if \(authFails >= 3\) \{
    el\.innerHTML = `Auth failed — check credentials\. <button id="retry-btn" onclick="retryAuth\(\)">Retry</button>`;
  \} else \{
    el\.textContent = 'Error: ' \+ msg;
  \}
\}

window\.retryAuth = function\(\) \{
  authFails = 0;""",
    r"""function updateApiStatusError(msg) {
  const el = document.getElementById('api-status');
  el.className = 'text-xs font-medium dn';
  if (_authFailCount >= 3) {
    el.innerHTML = `Auth failed — check credentials. <button id="retry-btn" onclick="retryAuth()">Retry</button>`;
  } else {
    el.textContent = 'Error: ' + msg;
  }
}

window.retryAuth = function() {
  _authFailCount = 0;""",
    content
)


# Fix refresh function
content = re.sub(
    r"""async function refresh\(\) \{
  try \{
    if \(authFails >= 3\) return; // Stop hammering
    if \(\!tk\) await autoLogin\(\);

    document\.getElementById\('api-status'\)\.textContent = 'Connected';
    document\.getElementById\('api-status'\)\.className = 'text-xs font-medium up';

    // Caching for infrequently changing data \(5 min\)
    const now = Date\.now\(\);
    if \(\!cachedConfig || \!cachedWhitelist || now - lastCacheUpdate > 300000\) \{
      \[cachedConfig, cachedWhitelist\] = await Promise\.all\(\[
        api\('show_config'\), api\('whitelist'\)
      \]\);
      lastCacheUpdate = now;
    \}

    const \[statusOpen, profitRaw, tradesResp, balData, daily\] = await Promise\.all\(\[
      api\('status'\), api\('profit'\), api\('trades\?limit=100'\), api\('balance'\), api\('daily\?timescale=30'\)
    \]\);""",
    r"""async function refresh() {
  try {
    if (!tk) await autoLogin();

    document.getElementById('api-status').textContent = 'Connected';
    document.getElementById('api-status').className = 'text-xs font-medium up';

    const [statusOpen, profitRaw, cachedConfig, cachedWhitelist, tradesResp, balData, daily] = await Promise.all([
      api('status'), api('profit'), getCfg(), getWl(), api('trades?limit=100'), api('balance'), api('daily?timescale=30')
    ]);""",
    content
)

# Fix tick function
content = re.sub(
    r"""async function tick\(\) \{
  if \(_refreshing\) return;
  _refreshing = true;
  try \{ await refresh\(\); \}
  finally \{ _refreshing = false; \}
\}""",
    r"""async function tick() {
  if (_authFailCount >= 3) {
    updateApiStatusError("auth");
    return;
  }
  if (_refreshing) return;
  _refreshing = true;
  try { await refresh(); }
  finally { _refreshing = false; }
}""",
    content
)

with open('user_data/dashboard_full.html', 'w') as f:
    f.write(content)
