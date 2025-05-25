// js/upgrade-main.js

const API_BASE = "https://sfl-9cb8.onrender.com";
const emailSpan = document.getElementById("userEmail");
const baseStatsEl = document.getElementById("base_stats");
const remainingPointsEl = document.getElementById("remainingPoints");
let userId = null;
let userStats = {};
let alloc = { hp: 0, attack: 0, luck: 0, atk_speed: 0 };

const statFields = [
  { key: "hp", label: "生命值（+5）" },
  { key: "attack", label: "攻擊力" },
  { key: "luck", label: "運氣值" },
  { key: "atk_speed", label: "攻擊速度" }
];

function showLoading(show) {
  const loading = document.getElementById("loadingOverlay");
  if (!loading) return;
  loading.style.display = show ? "flex" : "none";
}

function logout() {
  firebase.auth().signOut().then(() => {
    window.parent.location.href = "/SFL/login.html";
  });
}

function renderStats() {
  if (!userStats.base_stats) return;
  const stats = userStats.base_stats;
  document.getElementById("currentStats").innerHTML = `
    <li>生命值：${stats.hp}</li>
    <li>攻擊力：${stats.attack}</li>
    <li>運氣值：${stats.luck}</li>
    <li>攻擊速度：${stats.atk_speed}</li>
  `;

  let html = "";
  statFields.forEach(({ key, label }) => {
    html += `
      <div class="stat-control">
        <label>${label}：</label>
        <button onclick="adjust('${key}', -1)">-</button>
        <span id="val_${key}">${alloc[key]}</span>
        <button onclick="adjust('${key}', 1)">+</button>
      </div>
    `;
  });
  baseStatsEl.innerHTML = html;
  remainingPointsEl.innerText = userStats.stat_points;
}

function adjust(stat, delta) {
  const current = alloc[stat];
  const totalAlloc = Object.values(alloc).reduce((a, b) => a + b, 0);
  const remaining = userStats.stat_points - totalAlloc;
  if (delta > 0 && remaining > 0) alloc[stat]++;
  else if (delta < 0 && current > 0) alloc[stat]--;
  document.getElementById(`val_${stat}`).innerText = alloc[stat];
  remainingPointsEl.innerText = userStats.stat_points - Object.values(alloc).reduce((a, b) => a + b, 0);
}

function resetStats() {
  Object.keys(alloc).forEach(k => {
    alloc[k] = 0;
    document.getElementById(`val_${k}`).innerText = "0";
  });
  remainingPointsEl.innerText = userStats.stat_points;
}

async function submitLevelUp() {
  const totalAlloc = Object.values(alloc).reduce((a, b) => a + b, 0);
  if (totalAlloc === 0) return alert("請先分配能力點！");
  if (!confirm("是否確定分配能力點？")) return;

  showLoading(true);
  try {
    const res = await fetch(`${API_BASE}/levelup`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user: userId, allocate: alloc })
    });
    const data = await res.json();
    if (res.ok) {
      alert("能力值已分配完畢！");
      await loadStatus();
      resetStats();
    } else alert(data.error || "升級失敗");
  } catch (err) {
    alert(`錯誤：${err.message}`);
  } finally {
    showLoading(false);
  }
}

let skillAlloc = {};
let remainingSkillPoints = 0;
let originalSkills = {};
let allSkills = [];

async function loadAllSkills() {
  try {
    const skillRes = await fetch(`${API_BASE}/skills_full`);
    const userRes = await fetch(`${API_BASE}/status?user=${encodeURIComponent(userId)}`);
    allSkills = await skillRes.json();
    userStats = await userRes.json();

    skillAlloc = {};
    remainingSkillPoints = userStats.skill_points;
    originalSkills = { ...userStats.skills };

    allSkills.forEach(skill => {
      skillAlloc[skill.id] = userStats.skills?.[skill.id] ?? 0;
    });

    renderStats();
    renderSkills();
  } catch (err) {
    alert(`載入技能錯誤：${err.message}`);
  }
}

function renderSkills() {
  const container = document.getElementById("skillList");
  container.innerHTML = "";
  const typeMap = { atk: "【輸出型】", heal: "【治療型】", buff: "【增益型】" };
  const elemMap = { pyro: "火", hydro: "水", nature: "自然", electro: "雷", light: "光", dark: "暗", all: "全", none: "無" };

  allSkills.sort((a, b) => (a.sort || 999) - (b.sort || 999)).forEach(skill => {
    const currentLevel = skillAlloc[skill.id] ?? 0;
    const isLocked = userStats.level < skill.learnlvl;
    const row = document.createElement("div");
    row.className = "skill-card";
    const effectiveMultiplier = (skill.multiplier + (currentLevel - 1) * (skill.multiplierperlvl || 0)).toFixed(2);
    row.innerHTML = `
      <strong>${typeMap[skill.type] || "【其他】"} ${skill.name}</strong>
      <p>${skill.description}</p>
      <p>等級上限：${skill.maxlvl}　冷卻：${skill.cd} 回合</p>
      <p>倍率：${effectiveMultiplier} 屬性：${skill.element.map(e => elemMap[e] || e).join("/")}</p>
    `;
    const controls = document.createElement("div");
    controls.className = "skill-controls";
    const minus = document.createElement("button");
    minus.textContent = "-";
    minus.disabled = currentLevel === 0 || isLocked;
    minus.onclick = () => {
      if (skillAlloc[skill.id] > 0) {
        skillAlloc[skill.id]--;
        remainingSkillPoints++;
        renderSkills();
      }
    };
    const value = document.createElement("span");
    value.textContent = currentLevel;
    value.style.margin = "0 8px";
    value.style.color = "#ffd93d";
    value.style.fontWeight = "bold";
    const plus = document.createElement("button");
    plus.textContent = "+";
    plus.disabled = isLocked || currentLevel >= skill.maxlvl || remainingSkillPoints <= 0;
    plus.onclick = () => {
      if (skillAlloc[skill.id] < skill.maxlvl && remainingSkillPoints > 0) {
        skillAlloc[skill.id]++;
        remainingSkillPoints--;
        renderSkills();
      }
    };
    controls.appendChild(minus);
    controls.appendChild(value);
    controls.appendChild(plus);
    row.appendChild(controls);
    if (isLocked) {
      const tip = document.createElement("span");
      tip.style.color = "red";
      tip.style.marginLeft = "10px";
      tip.textContent = `等級達至 ${skill.learnlvl} 等解鎖`;
      row.appendChild(tip);
    }
    container.appendChild(row);
  });
  document.getElementById("remainingSkillPoints").innerText = remainingSkillPoints;
}

function resetSkills() {
  allSkills.forEach(skill => {
    skillAlloc[skill.id] = 0;
  });
  const spentPoints = Object.values(userStats.skills || {}).reduce((sum, val) => sum + (val || 0), 0);
  remainingSkillPoints = userStats.skill_points + spentPoints;
  renderSkills();
}

async function submitSkills() {
  showLoading(true);
  try {
    const payload = { user: userId, skills: skillAlloc };
    const res = await fetch(`${API_BASE}/skills_save`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (res.ok) {
      alert("技能儲存成功！");
      await loadStatus();
      await loadAllSkills();
    } else {
      alert(data.error || "儲存失敗");
    }
  } catch (err) {
    alert(`錯誤：${err.message}`);
  } finally {
    showLoading(false);
  }
}

async function waitForUser() {
  return new Promise(resolve => {
    const unsub = firebase.auth().onAuthStateChanged(user => {
      unsub();
      resolve(user);
    });
  });
}

async function loadStatus() {
  if (!userId) return;
  showLoading(true);
  try {
    const res = await fetch(`${API_BASE}/status?user=${encodeURIComponent(userId)}`);
    const data = await res.json();
    userStats = data;
    renderStats();
  } catch (err) {
    console.error("載入錯誤：", err.message);
  } finally {
    showLoading(false);
  }
}

async function runMain() {
  showLoading(true);
  const user = await waitForUser();
  if (!user) {
    window.parent.location.href = "/SFL/login.html";
    return;
  }
  userId = user.email;
  const res = await fetch(`${API_BASE}/status?user=${encodeURIComponent(userId)}`);
  const data = await res.json();
  await loadStatus();
  await loadAllSkills();
  window.parent.postMessage({ nickname: data.nickname || userId }, "*");
  showLoading(false);
}

window.logout = logout;
window.adjust = adjust;
window.resetStats = resetStats;
window.submitLevelUp = submitLevelUp;
window.resetSkills = resetSkills;
window.submitSkills = submitSkills;
window.runMain = runMain;
