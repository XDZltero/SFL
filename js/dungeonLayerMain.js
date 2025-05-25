// ✅ 完整修正版 js/dungeonLayerMain.js

const elementMap = {
  none: "無", phy: "物理", pyro: "火", hydro: "水", electro: "雷",
  nature: "自然", light: "光", dark: "暗", all: "全"
};

const API = "https://sfl-9cb8.onrender.com";
const params = new URLSearchParams(location.search);
const dungeon = params.get("dungeon");
let layer = parseInt(params.get("layer") || "0");
let userId = null;

let levelExp = {};
let currentProgress = 0;

const logArea = document.getElementById("logArea");
const userDiv = document.getElementById("userInfo");
const monsterDiv = document.getElementById("monsterInfo");
const retryBtn = document.getElementById("retryBtn");
const nextBtn = document.getElementById("nextBtn");
const leaveBtn = document.getElementById("leaveBtn");
const battleBtn = document.getElementById("battleBtn");

function showLoading(show) {
  const loading = document.getElementById("loadingOverlay");
  if (loading) loading.style.display = show ? "flex" : "none";
}

function hidePageLoading() {
  if (window.pageLoadingInterval) clearInterval(window.pageLoadingInterval);
  setTimeout(() => showLoading(false), 500);
}

function showBattleLoading(show) {
  const overlay = document.getElementById("battleLoadingOverlay");
  if (!overlay) return;
  overlay.style.display = show ? "flex" : "none";
  if (show) {
    createParticles();
    startLoadingProgress();
  }
}

function createParticles() {
  const container = document.getElementById("particles");
  if (!container) return;
  container.innerHTML = "";
  for (let i = 0; i < 50; i++) {
    const p = document.createElement("div");
    p.className = "particle";
    p.style.left = Math.random() * 100 + "%";
    p.style.animationDelay = Math.random() * 6 + "s";
    p.style.animationDuration = Math.random() * 3 + 3 + "s";
    container.appendChild(p);
  }
}

function startLoadingProgress() {
  const progressBar = document.getElementById("progressBar");
  const progressText = document.getElementById("progressText");
  const loadingText = document.getElementById("loadingText");
  const messages = [
    "正在分析戰況...",
    "計算屬性克制...",
    "準備戰鬥技能...",
    "載入怪物資料...",
    "初始化戰鬥系統...",
    "準備就緒！"
  ];
  let progress = 0;
  let messageIndex = 0;
  const interval = setInterval(() => {
    progress += Math.random() * 15 + 5;
    if (progress > 100) progress = 100;
    progressBar.style.width = progress + "%";
    progressText.textContent = Math.round(progress) + "%";
    if (messageIndex < messages.length - 1 && progress > (messageIndex + 1) * 15) {
      messageIndex++;
      loadingText.textContent = messages[messageIndex];
    }
    if (progress >= 100) {
      clearInterval(interval);
      loadingText.textContent = messages[messages.length - 1];
    }
  }, 200);
  window.battleProgressInterval = interval;
}

function percent(n) { return Math.round(n * 100) + "%"; }
function calcTotal(base, equip) { return (base || 0) + (equip || 0); }

function formatStats(data) {
  const b = data.base_stats;
  const e = data.equipment_stats || {};
  const progress = percent(data.exp / (levelExp[data.level] || 1));
  return `玩家名稱: ${data.nickname}
等級: ${data.level}
經驗值: ${data.exp} (${progress})
生命值: ${calcTotal(b.hp, e.hp)} (${b.hp} + ${e.hp || 0})
攻擊力: ${calcTotal(b.attack, e.attack)} (${b.attack} + ${e.attack || 0})
護盾值: ${calcTotal(b.shield, e.shield)} (${b.shield} + ${e.shield || 0})
幸運值: ${calcTotal(b.luck, e.luck)} (${b.luck} + ${e.luck || 0})
命中率: ${percent(calcTotal(b.accuracy, e.accuracy))} (${b.accuracy} + ${e.accuracy || 0})
迴避率: ${percent(calcTotal(b.evade, e.evade))} (${b.evade} + ${e.evade || 0})
攻擊速度: ${b.atk_speed}
額外傷害加成: ${b.other_bonus ?? 0}
剩餘能力值點數: ${data.stat_points}
剩餘技能點: ${data.skill_points}`;
}

async function fetchUser(id) {
  const res = await fetch(`${API}/status?user=${encodeURIComponent(id)}`);
  return await res.json();
}

async function fetchMonster(id) {
  const res = await fetch(`${API}/monster?id=${encodeURIComponent(id)}`);
  return await res.json();
}

async function fetchProgress() {
  const res = await fetch(`${API}/get_progress?user=${encodeURIComponent(userId)}`);
  const data = await res.json();
  currentProgress = data.progress?.[dungeon] ?? 0;
  return data;
}

function checkProgressBeforeBattle() {
  if (layer > currentProgress) {
    alert("層數異常（尚未解鎖），請重新進入副本");
    window.parent.loadPage("dungeons.html");
    return false;
  }
  return true;
}

function showBossCss() {
  document.body.classList.add("boss-mode");
  ["battleLoadingOverlay", "battleIcon", "battleTitle", "loadingText", "progressContainer", "progressText", "battleTips", "monsterInfo"]
    .forEach(id => document.getElementById(id)?.classList.add("boss"));
}

async function loadLayer() {
  const expRes = await fetch(`${API}/exp_table`);
  levelExp = await expRes.json();
  const user = await fetchUser(userId);
  userDiv.innerText = formatStats(user);
  const dungeonRes = await fetch(`${API}/dungeon_table`);
  const all = await dungeonRes.json();
  const current = all.find(d => d.id === dungeon);
  if (!current) throw new Error("副本不存在");

  const isBoss = layer === current.monsters.length;
  const monsterId = isBoss ? current.bossId : current.monsters[layer];
  const mon = await fetchMonster(monsterId);

  if (isBoss) showBossCss();

  monsterDiv.innerHTML = `
    <h2>${mon.name}</h2>
    <img src="${mon.image_url}" width="200"><br>
    <p>${mon.info}</p>
    <ul>
      <li>等級：${mon.level}</li>
      <li>屬性：${Array.isArray(mon.element) ? mon.element.map(e => elementMap[e] || e).join("、") : (elementMap[mon.element] || mon.element)}</li>
      <li>生命值：${mon.stats.hp}</li>
      <li>攻擊力：${mon.stats.attack}</li>
      <li>命中率：${Math.round(mon.stats.accuracy * 100)}%</li>
      <li>迴避率：${Math.round(mon.stats.evade * 100)}%</li>
      <li>攻擊速度：${mon.stats.atk_speed}</li>
    </ul>
  `;
  hidePageLoading();
}

function playBattleLog(log) {
  return new Promise(resolve => {
    logArea.innerHTML = "";
    let roundIndex = 0, actionIndex = 0;
    const player = setInterval(() => {
      if (roundIndex >= log.length) {
        clearInterval(player);
        resolve();
        return;
      }
      const round = log[roundIndex];
      if (actionIndex === 0) {
        const title = document.createElement("div");
        title.innerHTML = `<span style='color:#ffa500'>────────────── 第 ${round.round} 回合 ──────────────</span>`;
        logArea.appendChild(title);
      }
      const entry = document.createElement("div");
      entry.textContent = round.actions[actionIndex];
      logArea.appendChild(entry);
      logArea.scrollTop = logArea.scrollHeight;
      actionIndex++;
      if (actionIndex >= round.actions.length) {
        roundIndex++;
        actionIndex = 0;
      }
    }, 400);
  });
}

window.startBattle = async function () {
  if (!checkProgressBeforeBattle()) return;
  showBattleLoading(true);
  logArea.innerHTML = "";
  retryBtn.style.display = "none";
  nextBtn.style.display = "none";
  battleBtn.style.display = "none";
  leaveBtn.style.display = "none";

  try {
    const res = await fetch(`${API}/battle_dungeon`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user: userId, dungeon, layer })
    });
    const data = await res.json();
    const log = data.battle_log;
    if (!Array.isArray(log)) throw new Error("無戰鬥紀錄");

    setTimeout(() => {
      showBattleLoading(false);
      playBattleLog(log).then(() => {
        if (data.success) {
          fetchUser(userId).then(updated => userDiv.innerText = formatStats(updated));
          if (data.is_last_layer) leaveBtn.style.display = "inline-block";
          else {
            retryBtn.style.display = "inline-block";
            nextBtn.style.display = "inline-block";
          }
          logArea.innerHTML += `
            <div><span style='color:green'>🌟 戰鬥勝利！</span></div>
            <div><strong>🎁 戰利品：</strong><br>EXP + ${data.rewards.exp}</div>
          `;
        } else {
          logArea.innerHTML += `
            <div><span style='color:red'>☠️ 戰鬥失敗</span></div>
            <div>${data.message}</div>
          `;
          leaveBtn.style.display = "inline-block";
        }
      });
    }, 1000);
  } catch (err) {
    showBattleLoading(false);
    logArea.innerHTML = "❌ 錯誤：" + err.message;
    leaveBtn.style.display = "inline-block";
  }
};

window.addEventListener("message", async (e) => {
  if (e.data?.user) {
    userId = e.data.user;
    await fetchProgress();
    await loadLayer();
  }
});
