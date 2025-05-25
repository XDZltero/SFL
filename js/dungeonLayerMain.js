// ✅ 完整版 js/dungeonLayerMain.js（包含戰鬥流程與戰利品顯示）

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

async function fetchUser(userId) {
  const res = await fetch(`${API}/status?user=${encodeURIComponent(userId)}`);
  return await res.json();
}

async function fetchMonster(monsterId) {
  const res = await fetch(`${API}/monster?id=${encodeURIComponent(monsterId)}`);
  return await res.json();
}

function percent(n) { return Math.round(n * 100) + "%"; }
function calcTotal(base, equip) { return (base || 0) + (equip || 0); }

function formatStats(data) {
  const b = data.base_stats;
  const e = data.equipment_stats || {};
  const total_hp = calcTotal(b.hp, e.hp);
  const total_atk = calcTotal(b.attack, e.attack);
  const total_shield = calcTotal(b.shield, e.shield);
  const total_luck = calcTotal(b.luck, e.luck);
  const total_accuracy = calcTotal(b.accuracy, e.accuracy);
  const total_evade = calcTotal(b.evade, e.evade);
  const progress = percent(data.exp / (levelExp[data.level] || 1));
  return `玩家名稱: ${data.nickname}
等級: ${data.level}
經驗值: ${data.exp} (${progress})
生命值: ${total_hp} (${b.hp} + ${e.hp || 0})
攻擊力: ${total_atk} (${b.attack} + ${e.attack || 0})
護盾值: ${total_shield} (${b.shield} + ${e.shield || 0})
幸運值: ${total_luck} (${b.luck} + ${e.luck || 0})
命中率: ${percent(total_accuracy)} (${b.accuracy} + ${e.accuracy || 0})
迴避率: ${percent(total_evade)} (${b.evade} + ${e.evade || 0})
攻擊速度: ${b.atk_speed}
額外傷害加成: ${b.other_bonus ?? 0}
剩餘能力值點數: ${data.stat_points}
剩餘技能點: ${data.skill_points}`;
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

function showBattleResult(data, logArea) {
  const winMsg = document.createElement("div");
  winMsg.innerHTML = "<span style='color:green'>═══════════════ 🌟 戰鬥結束 🌟 ═══════════════</span>";
  logArea.appendChild(winMsg);

  if (!data.rewards) return;
  const { exp, leveled_up, drops } = data.rewards;
  fetch(`${API}/items`).then(res => res.json()).then(itemMap => {
    const rewardLog = document.createElement("div");
    rewardLog.innerHTML = `<br><strong>🎁 戰利品：</strong><br>EXP + ${exp}` +
      (leveled_up ? `<br><span style='color:red'>等級提升！</span>` : "") +
      drops.filter(d => Math.random() <= d.rate).map(d => {
        const meta = itemMap[d.id] || { name: d.id, special: 0 };
        if (meta.special === 2) return `<br><span style='color:crimson'>【超稀有】${meta.name} × ${d.value}</span>`;
        if (meta.special === 1) return `<br><span style='color:cornflowerblue'>【稀有】${meta.name} × ${d.value}</span>`;
        return `<br>${meta.name} × ${d.value}`;
      }).join("");
    logArea.appendChild(rewardLog);
  });
}

function showBattleFail(data, logArea) {
  const loseMsg = document.createElement("div");
  loseMsg.innerHTML = "<span style='color:red'>═══════════════ ☠️ 你已戰敗 ☠️ ═══════════════</span>";
  logArea.appendChild(loseMsg);
  const failMsg = document.createElement("div");
  failMsg.innerHTML = `<br>❌ ${data.message}`;
  logArea.appendChild(failMsg);
}

async function startBattle() {
  if (!checkProgressBeforeBattle()) return;
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
    if (!Array.isArray(log)) throw new Error("伺服器未回傳戰鬥紀錄");

    playBattleLog(log).then(() => {
      if (data.success) {
        fetchUser(userId).then(updatedUser => {
          userDiv.innerText = formatStats(updatedUser);
        });
        if (data.is_last_layer) {
          leaveBtn.style.display = "inline-block";
        } else {
          retryBtn.style.display = "inline-block";
          nextBtn.style.display = "inline-block";
        }
        showBattleResult(data, logArea);
      } else {
        leaveBtn.style.display = "inline-block";
        showBattleFail(data, logArea);
      }
    });
  } catch (err) {
    logArea.innerHTML = "❌ 錯誤：無法完成戰鬥<br>" + err.message;
    leaveBtn.style.display = "inline-block";
  }
}

function playBattleLog(log) {
  return new Promise(resolve => {
    logArea.innerHTML = "";
    let roundIndex = 0;
    let actionIndex = 0;
    function nextLine() {
      if (roundIndex >= log.length) return resolve();
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
    }
    const interval = setInterval(nextLine, 400);
  });
}

window.startBattle = startBattle;

window.addEventListener("message", async (e) => {
  if (e.data?.user) {
    userId = e.data.user;
    await fetchProgress();
    const expRes = await fetch(`${API}/exp_table`);
    levelExp = await expRes.json();
    await loadLayer();
  }
});

async function loadLayer() {
  const dungeonRes = await fetch(`${API}/dungeon_table`);
  const all = await dungeonRes.json();
  const current = all.find(d => d.id === dungeon);
  if (!current) return;

  const user = await fetchUser(userId);
  userDiv.innerText = formatStats(user);

  const isBoss = layer === current.monsters.length;
  const monId = isBoss ? current.bossId : current.monsters[layer];
  const mon = await fetchMonster(monId);

  monsterDiv.innerHTML = `
    <h2>${mon.name}</h2>
    <img src="${mon.image_url}" width="200"><br>
    <p>${mon.info}</p>
    <ul>
      <li>等級：${mon.level}</li>
      <li>屬性：${Array.isArray(mon.element) ? mon.element.map(e => elementMap[e] || e).join("、") : elementMap[mon.element]}</li>
      <li>生命值：${mon.stats.hp}</li>
      <li>攻擊力：${mon.stats.attack}</li>
      <li>命中率：${Math.round(mon.stats.accuracy * 100)}%</li>
      <li>迴避率：${Math.round(mon.stats.evade * 100)}%</li>
      <li>攻擊速度：${mon.stats.atk_speed}</li>
    </ul>
  `;
  hidePageLoading();
}
