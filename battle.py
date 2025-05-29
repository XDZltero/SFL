// 在 dungeon_layer.html 中，找到戰利品顯示部分並替換為以下代碼：

if (data.rewards) {
  const { 
    base_exp, 
    exp_luck_bonus, 
    leveled_up, 
    actual_drops, 
    drop_luck_bonus 
  } = data.rewards;
  
  fetch(`${API}/items_table`).then(res => res.json()).then(itemMap => {
    const rewardLog = document.createElement("div");
    rewardLog.className = "reward-message";
    
    // 💎 經驗值顯示
    let expText = `<br><strong>🎁 戰利品：</strong><br>EXP + ${base_exp}`;
    
    // 🌟 經驗值幸運加成
    if (exp_luck_bonus > 0) {
      expText += `<br><span style='color:gold;text-shadow:0 0 5px rgba(255,215,0,0.8);font-weight:bold;'>🍀 幸運加成！額外獲得 ${exp_luck_bonus} 經驗值！</span>`;
    }
    
    // 🎊 等級提升
    if (leveled_up) {
      expText += `<br><span style='color:red'>🎊 等級提升！</span>`;
    }
    
    let dropText = "";
    
    // 🎯 顯示實際獲得的物品
    if (actual_drops && Object.keys(actual_drops).length > 0) {
      dropText += "<br><strong>📦 獲得物品：</strong>";
      
      for (const [itemId, quantity] of Object.entries(actual_drops)) {
        const meta = itemMap[itemId] || { name: itemId, special: 0 };
        const luckBonus = drop_luck_bonus[itemId] || 0;
        const baseQuantity = quantity - luckBonus;
        
        let itemText = "";
        
        // 根據稀有度設定顏色
        if (meta.special === 2) {
          itemText = `<br><span style='color:crimson'>【超稀有】${meta.name} × ${quantity}</span>`;
        } else if (meta.special === 1) {
          itemText = `<br><span style='color:cornflowerblue'>【稀有】${meta.name} × ${quantity}</span>`;
        } else {
          itemText = `<br>${meta.name} × ${quantity}`;
        }
        
        // 🌟 如果有幸運加成，額外顯示
        if (luckBonus > 0) {
          itemText += `<br><span style='color:gold;text-shadow:0 0 5px rgba(255,215,0,0.8);font-weight:bold;margin-left:20px;'>🍀 幸運加成！額外獲得 ${luckBonus} 個！</span>`;
        }
        
        dropText += itemText;
      }
    } else {
      dropText += "<br>本次沒有掉落物品";
    }
    
    rewardLog.innerHTML = expText + dropText;
    logArea.appendChild(rewardLog);
    scrollLogToBottom();
  });
}
