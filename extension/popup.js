const $ = (id) => document.getElementById(id);
const WORDS = {
  connected: 'Connected to Nova',
  offline: 'Not connected',
  'no-token': 'Needs pairing',
};

async function show() {
  const { status } = await chrome.storage.session.get('status');
  const s = status || { state: 'offline', detail: 'starting' };
  $('status').className = s.state;
  $('status').innerHTML = `<b>${WORDS[s.state] || s.state}</b>${s.detail ? ` · ${s.detail.replace(/</g, '&lt;')}` : ''}`;
}

(async () => {
  const { port = 8765, token = '' } = await chrome.storage.local.get(['port', 'token']);
  $('port').value = port;
  $('token').value = token;
  show();
})();

chrome.storage.onChanged.addListener((changes, area) => area === 'session' && changes.status && show());

$('save').onclick = () => chrome.storage.local.set({
  port: Number($('port').value) || 8765,
  token: $('token').value.trim(),
});
$('reconnect').onclick = () => chrome.runtime.sendMessage({ type: 'reconnect' });
