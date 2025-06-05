import random
from firebase_admin import firestore
import json
import copy
import time

# 獲得裝備卡片數值
with open("parameter/equips.json", "r", encoding="utf-8") as f:
    equip_data = json.load(f)

def get_equipment_bonus(equipment):
    bonus = {}
    
    # 確保 equipment 不是 None
    if not equipment:
        return bonus
        
    for slot in equipment:
        card_info = equipment[slot]
        
        # 檢查槽位是否為空 (None 或 空字典)
        if not card_info:
            continue
            
        # 確保 card_info 是字典類型
        if not isinstance(card_info, dict):
            continue
            
        # 檢查是否有卡片資訊
        if not card_info.keys():
            continue
            
        try:
            card_id = list(card_info.keys())[0]
            level = card_info[card_id]
            
            # 檢查等級是否有效
            if not level or level <= 0:
                continue
            
            card = next((c for c in equip_data if c["id"] == card_id), None)
            if not card:
                continue
            
            level_stats = card["value"].get(str(level), {})
            for stat, val in level_stats.items():
                bonus[stat] = bonus.get(stat, 0) + val
                
        except (IndexError, KeyError, TypeError) as e:
            # 如果處理單個裝備時出錯，跳過這個裝備但繼續處理其他裝備
            print(f"警告：處理裝備槽位 {slot} 時出錯: {e}")
            continue
    
    return bonus

# 命中計算
def calculate_hit(attacker_acc, defender_evade, attacker_luck):
    # 命中率 - 迴避率 + 運氣補正，每點 luck +1%
    hit_chance = attacker_acc - defender_evade + (attacker_luck * 0.002)
    hit_chance = min(max(hit_chance, 0.05), 0.99)
    return random.random() < hit_chance

# 傷害計算
def calculate_damage(base_atk, skill_multiplier, bonus, shield, penetrate=0):
    """
    計算傷害
    Args:
        base_atk: 基礎攻擊力
        skill_multiplier: 技能倍率
        bonus: 額外傷害加成
        shield: 護盾值
        penetrate: 護盾穿透值
    """
    raw = base_atk * skill_multiplier * (1 + bonus)
    
    # 護盾穿透計算：護盾值 - 穿透值，最低為0
    effective_shield = max(0, shield - penetrate)
    
    # 護盾減傷計算（原有邏輯）
    reduction = min(effective_shield * 0.001, 0.99)
    
    return int(raw * (1 - reduction))

# 屬性克制檔案
def load_element_table():
    with open("parameter/attribute_table.json", "r", encoding="utf-8") as f:
        return json.load(f)
ELEMENT_TABLE = load_element_table()

# 屬性克制計算
def get_element_multiplier(attacker_elements, defender_elements):
    if not isinstance(attacker_elements, list):
        attacker_elements = [attacker_elements]
    if not isinstance(defender_elements, list):
        defender_elements = [defender_elements]

    total = 0
    count = 0

    for atk in attacker_elements:
        for defn in defender_elements:
            mult = ELEMENT_TABLE["advantage"].get(atk, {}).get(defn, 1.0)
            total += mult
            count += 1

    return total / count if count > 0 else 1.0

def get_user_item_ref(db, user_id):
    return db.collection("user_items").document(user_id)

def apply_drops(db, user_id, drops, user_luck=0):
    """
    處理掉落物品，並回傳實際掉落資訊
    三輪掉落邏輯：
    - 第一輪：原始掉落率 + 幸運加成(每點幸運+0.2%，最大+25%)
    - 第二輪：只有第一輪掉落的道具才能進行第二輪判定(每點幸運+0.15%，最大+15%)  
    - 第三輪：只有第二輪掉落的道具才能進行第三輪判定(每點幸運+0.05%，最大+10%)
    """
    ref = get_user_item_ref(db, user_id)
    snap = ref.get()
    current = snap.to_dict() if snap.exists else {"id": user_id, "items": {}}

    # 🎲 計算三輪的幸運加成
    round_1_bonus_rate = min(user_luck * 0.002, 0.25)   # 最大 25% 額外機率
    round_2_bonus_rate = min(user_luck * 0.0015, 0.15)  # 最大 15% 純幸運機率
    round_3_bonus_rate = min(user_luck * 0.0005, 0.10)  # 最大 10% 純幸運機率
    
    # 記錄各輪掉落
    actual_drops = {}
    round_1_bonus = {}
    round_2_bonus = {}  
    round_3_bonus = {}
    
    # 記錄每輪成功掉落的道具
    round_1_dropped = []
    round_2_dropped = []
    
    # 🎯 第一輪：基礎掉落 + 第一輪幸運加成
    for drop in drops:
        base_rate = drop["rate"]
        item_id = drop["id"]
        qty = drop["value"]
        
        # 計算第一輪掉落率（基礎率 + 幸運加成）
        if base_rate >= 1.0:
            # 100% 掉落率不變
            round_1_rate = 1.0
        else:
            # 非100%掉落率進行幸運加成，上限95%
            round_1_rate = min(base_rate * (1 + round_1_bonus_rate), 0.95)
        
        if random.random() <= round_1_rate:
            # 第一輪掉落成功
            current_amount = current["items"].get(item_id, 0)
            new_amount = current_amount + qty
            
            if new_amount > 999:
                actual_received = 999 - current_amount
                current["items"][item_id] = 999
                if actual_received > 0:
                    actual_drops[item_id] = actual_drops.get(item_id, 0) + actual_received
                    round_1_dropped.append(drop)  # 記錄成功掉落的道具
            else:
                current["items"][item_id] = new_amount
                actual_drops[item_id] = actual_drops.get(item_id, 0) + qty
                round_1_dropped.append(drop)  # 記錄成功掉落的道具

    for drop in round_1_dropped:
        item_id = drop["id"]
        qty = drop["value"]
        
        # ✅ 修正：第二輪使用純幸運機率，不加原始掉落率
        round_2_rate = round_2_bonus_rate
        
        if random.random() <= round_2_rate:
            # 第二輪掉落成功
            current_amount = current["items"].get(item_id, 0)
            new_amount = current_amount + qty
            
            if new_amount > 999:
                bonus_qty = max(0, 999 - current_amount)
                current["items"][item_id] = 999
            else:
                bonus_qty = qty
                current["items"][item_id] = new_amount
            
            if bonus_qty > 0:
                round_2_bonus[item_id] = round_2_bonus.get(item_id, 0) + bonus_qty
                actual_drops[item_id] = actual_drops.get(item_id, 0) + bonus_qty
                round_2_dropped.append(drop)  # 記錄成功掉落的道具

    # 🌟 第三輪：只對第二輪掉落的道具進行純幸運判定
    for drop in round_2_dropped:
        item_id = drop["id"]
        qty = drop["value"]
        
        # ✅ 修正：第三輪使用純幸運機率，不加原始掉落率
        round_3_rate = round_3_bonus_rate
        
        if random.random() <= round_3_rate:
            # 第三輪掉落成功
            current_amount = current["items"].get(item_id, 0)
            new_amount = current_amount + qty
            
            if new_amount > 999:
                bonus_qty = max(0, 999 - current_amount)
                current["items"][item_id] = 999
            else:
                bonus_qty = qty
                current["items"][item_id] = new_amount
            
            if bonus_qty > 0:
                round_3_bonus[item_id] = round_3_bonus.get(item_id, 0) + bonus_qty
                actual_drops[item_id] = actual_drops.get(item_id, 0) + bonus_qty

    # 儲存更新後的道具
    ref.set(current)
    
    return {
        "items": actual_drops,
        "round_1_bonus": round_1_bonus,  # 第一輪沒有額外bonus，都算在基礎掉落中
        "round_2_bonus": round_2_bonus,  # 第二輪獲得的額外道具
        "round_3_bonus": round_3_bonus   # 第三輪獲得的額外道具
    }

# 等差增減傷計算
def level_damage_modifier(attacker_level, defender_level):
    diff = max(min(attacker_level - defender_level, 5), -5)
    if diff == 0:
        return 1.0
    elif abs(diff) == 1:
        return 1.05 if diff > 0 else 0.95
    elif abs(diff) == 2:
        return 1.075 if diff > 0 else 0.925
    elif abs(diff) == 3:
        return 1.10 if diff > 0 else 0.90
    elif abs(diff) == 4:
        return 1.15 if diff > 0 else 0.85
    elif abs(diff) >= 5:
        return 1.20 if diff > 0 else 0.80
    elif abs(diff) >= 6:
        return 1.30 if diff > 0 else 0.70
    elif abs(diff) >= 7:
        return 1.50 if diff > 0 else 0.50

# 確認是否升級
def check_level_up(user):
    with open("parameter/level_exp.json", "r", encoding="utf-8") as f:
        level_table = json.load(f)

    level = user["level"]
    exp = user["exp"]
    leveled = False

    while str(level) in level_table and exp >= level_table[str(level)] and level < 100:
        exp -= level_table[str(level)]
        level += 1
        user["stat_points"] += 5
        user["skill_points"] += 3
        leveled = True

    user["level"] = level
    user["exp"] = exp
    return leveled

# 怪物選擇技能
def pick_monster_skill(skills, skill_cd):
    valid_skills = [s for s in skills if skill_cd.get(s["id"], 0) == 0]
    r = random.random()
    total = 0
    for s in valid_skills:
        total += s.get("chance", 0)
        if r < total:
            return s
    return {
        "id": "basic_attack",
        "multiplier": 1.0,
        "description": "普通攻擊",
        "type": "atk",
        "element": ["none"]
    }

# buff技能
def init_stats_mod():
    return {
        "attack": 1.0,
        "shield": 1.0,
        "evade": 1.0,
        "accuracy": 1.0,
        "luck": 1.0,
        "atk_speed": 1.0,
        "all_damage": 1.0
    }

def apply_buffs(buffs, base_stats, log, is_user, actor_name):
    stats_mod = init_stats_mod()
    new_buffs = []
    temp_log = []

    buffs_copy = copy.deepcopy(buffs)

    for buff in buffs_copy:
        if buff["round"] > 0:
            effect = buff.get("effectType", "")
            multiplier = buff.get("multiplier", 1.0)
            if effect in stats_mod:
                stats_mod[effect] *= multiplier  # ✅ 確保這裡正確應用倍率

            buff["round"] -= 1
            if buff["round"] > 0:
                new_buffs.append(buff)
                name = buff.get("name", buff.get("description", "未知效果"))
                msg = f"你身上的 {name} 效果剩下 {buff['round']} 回合" if is_user else f"{actor_name} 的 {name} 效果剩下 {buff['round']} 回合"
                temp_log.append(msg)
            else:
                name = buff.get("name", buff.get("description", "未知效果"))
                msg = f"你身上的 {name} 效果已消失" if is_user else f"{actor_name} 施放的 {name} 效果已消失"
                temp_log.append(msg)

    return stats_mod, new_buffs, temp_log

def add_or_refresh_buff(buff_list, new_buff):
    for existing in buff_list:
        if existing["name"] == new_buff["name"]:
            existing["round"] = new_buff["round"]
            return
    buff_list.append(new_buff)

def add_or_refresh_debuff(debuff_list, new_debuff):
    for existing in debuff_list:
        if existing["name"] == new_debuff["name"]:
            existing["round"] = new_debuff["round"]
            return
    debuff_list.append(new_debuff)

# 新增：處理持續傷害效果
def add_dot_effect(dot_list, new_dot):
    # 持續傷害可以疊加，最多3層
    same_name_dots = [dot for dot in dot_list if dot["name"] == new_dot["name"]]
    if len(same_name_dots) >= 3:
        return  # 已達上限，不再添加
    dot_list.append(new_dot)

def apply_dot_effects(dot_effects, current_hp, max_hp, log, is_user, actor_name):
    new_dot_effects = []
    total_dot_damage = 0
    
    # ✅ 修正：使用累積HP來正確顯示
    running_hp = current_hp  # 追蹤當前HP
    
    for dot in dot_effects:
        if dot["round"] > 0:
            damage = dot["damage_per_turn"]
            total_dot_damage += damage
            
            # ✅ 修正：每次DOT傷害後更新running_hp
            running_hp = max(running_hp - damage, 0)
            
            dot["round"] -= 1
            if dot["round"] > 0:
                new_dot_effects.append(dot)
                target = "你" if is_user else actor_name
                log.append(f"{target}受到 {dot['name']} 的 {damage} 傷害（目前 HP：{running_hp}/{max_hp}），剩餘 {dot['round']} 回合")
            else:
                target = "你" if is_user else actor_name
                log.append(f"{target}受到 {dot['name']} 的 {damage} 傷害（目前 HP：{running_hp}/{max_hp}），{dot['name']}效果消失")
    
    return new_dot_effects, total_dot_damage

# 用作計算出手預設值
def get_buff_stats_only(buffs):
    stats_mod = init_stats_mod()
    for buff in buffs:
        if buff["round"] > 0:
            effect = buff.get("effectType", "")
            multiplier = buff.get("multiplier", 1.0)
            if effect in stats_mod:
                stats_mod[effect] *= multiplier
    return stats_mod

def simulate_battle(user, monster, user_skill_dict):
    log = []
    db = firestore.client()

    # ✅ 深拷貝 user
    user = copy.deepcopy(user)

    # ✅ 建立 battle 專用 base_stats（含裝備加成）
    raw_stats = user.get("base_stats", {})
    equipment = user.get("equipment", {})
    equip_bonus = get_equipment_bonus(equipment)

    user_battle_stats = {}
    for stat in set(list(raw_stats.keys()) + list(equip_bonus.keys())):
        user_battle_stats[stat] = raw_stats.get(stat, 0) + equip_bonus.get(stat, 0)

    user_hp = user_battle_stats["hp"]
    mon_hp = monster["stats"]["hp"]
    player_skill_cd = {k: 0 for k in user.get("skills", {})}
    monster_skill_cd = {s["id"]: 0 for s in monster.get("skills", [])}
    bondage_round = 0
    user_buffs = []
    mon_buffs = []
    
    # 新增狀態追蹤
    user_dot_effects = []  # 玩家身上的持續傷害
    mon_dot_effects = []   # 怪物身上的持續傷害
    user_invincible = 0    # 玩家無敵回合數
    mon_invincible = 0     # 怪物無敵回合數
    user_damage_shield = None  # 玩家傷害累積盾狀態
    mon_damage_shield = None   # 怪物傷害累積盾狀態

    turn_limit = 20 if monster.get("is_boss") else 10
    turns_used = 0

    user_stats_mod = init_stats_mod()
    mon_stats_mod = init_stats_mod()

    while user_hp > 0 and mon_hp > 0:
        turns_used += 1
        current_round = turns_used

        if turns_used > turn_limit:
            round_log = [f"⚠️ 已超過回合上限（{turn_limit} 回合），戰鬥失敗"]
            log.append({"round": current_round-1, "actions": round_log})
            user_hp = 0  # 強制設置玩家血量為0
            outcome = "lose"
            break
        
        round_log = []

        # 處理回合開始的持續傷害
        if user_dot_effects:
            user_dot_effects, dot_damage = apply_dot_effects(user_dot_effects, user_hp, user_battle_stats["hp"], round_log, True, "")
            user_hp = max(user_hp - dot_damage, 0)
            if user_hp <= 0:
                round_log.append(f"💀 你因持續傷害效果死亡")
                log.append({"round": current_round, "actions": round_log})
                break
                
        if mon_dot_effects:
            mon_dot_effects, dot_damage = apply_dot_effects(mon_dot_effects, mon_hp, monster["stats"]["hp"], round_log, False, monster["name"])
            mon_hp = max(mon_hp - dot_damage, 0)
            if mon_hp <= 0:
                round_log.append(f"💀 {monster['name']} 因持續傷害效果死亡")
                log.append({"round": current_round, "actions": round_log})
                break

        # 處理無敵狀態倒數
        if user_invincible > 0:
            user_invincible -= 1
            if user_invincible == 0:
                round_log.append("你的無敵狀態已消失")
                
        if mon_invincible > 0:
            mon_invincible -= 1
            if mon_invincible == 0:
                round_log.append(f"{monster['name']} 的無敵狀態已消失")

        # 處理傷害累積盾狀態
        if user_damage_shield:
            user_damage_shield["rounds"] -= 1
            if user_damage_shield["rounds"] <= 0:
                # 發動大招
                ultimate = user_damage_shield["ultimate"]
                round_log.append(f"你使用 {ultimate['name']} 對 {monster['name']} 造成 {ultimate['damage']} 傷害（對方 HP：{max(mon_hp - ultimate['damage'], 0)}/{monster['stats']['hp']}）")
                log.append({"round": current_round, "actions": round_log})
                mon_hp = max(mon_hp - ultimate["damage"], 0)
                user_damage_shield = None
                if mon_hp <= 0:
                    break
                    
        if mon_damage_shield:
            mon_damage_shield["rounds"] -= 1
            if mon_damage_shield["rounds"] <= 0:
                # 發動大招
                ultimate = mon_damage_shield["ultimate"]
                round_log.append(f"{monster['name']} 使用 {ultimate['name']} 對你造成 {ultimate['damage']} 傷害（目前 HP：{max(user_hp - ultimate['damage'], 0)}/{user_battle_stats['hp']}）")
                log.append({"round": current_round, "actions": round_log})
                user_hp = max(user_hp - ultimate["damage"], 0)
                mon_damage_shield = None
                if user_hp <= 0:
                    break

        # ✅ Step 1: 預覽 buff 結果，不會扣除回合數
        user_stats_mod_preview = get_buff_stats_only(user_buffs)
        mon_stats_mod_preview = get_buff_stats_only(mon_buffs)
        
        # ✅ Step 2: 根據預覽值決定速度與出手順序
        user_speed = user_battle_stats["atk_speed"] * user_stats_mod_preview["atk_speed"]
        mon_speed = monster["stats"]["atk_speed"] * mon_stats_mod_preview["atk_speed"]
        user_turns = max(1, round(user_speed / mon_speed))
        mon_turns = max(1, round(mon_speed / user_speed))
        
        action_order = []
        if user_speed >= mon_speed:
            action_order.extend(["user"] * user_turns + ["mon"] * mon_turns)
        else:
            action_order.extend(["mon"] * mon_turns + ["user"] * user_turns)
        
        # ✅ Step 3: 實際扣除 buff 回合數，並取得最新倍率
        user_stats_mod, user_buffs, buff_log_user = apply_buffs(user_buffs, user_battle_stats, log, True, "")
        mon_stats_mod, mon_buffs, buff_log_mon = apply_buffs(mon_buffs, monster["stats"], log, False, monster["name"])

        user_level_mod = level_damage_modifier(user["level"], monster["level"])
        mon_level_mod = level_damage_modifier(monster["level"], user["level"])

        for actor in action_order:
            if user_hp <= 0 or mon_hp <= 0:
                break
                
            # 檢查傷害累積盾是否阻止行動
            if actor == "user" and user_damage_shield:
                continue
            if actor == "mon" and mon_damage_shield:
                continue

            if actor == "user":
                if bondage_round > 0:
                    round_log.append(f"你處於無法行動狀態，剩下 {bondage_round} 回合")
                    bondage_round -= 1
                    continue
                
                for sid in player_skill_cd:
                    if player_skill_cd[sid] > 0:
                        player_skill_cd[sid] -= 1

                any_skill_used = False
                user_skill_levels = user.get("skills", {})
                sorted_skills = sorted(
                    [(sid, level) for sid, level in user_skill_levels.items() if sid in user_skill_dict],
                    key=lambda pair: user_skill_dict[pair[0]].get("sort", 9999)
                )

                for skill_id, level in sorted_skills:
                    if user_hp <= 0 or mon_hp <= 0:
                        break
                    if level <= 0 or player_skill_cd.get(skill_id, 0) > 0:
                        continue

                    skill = user_skill_dict.get(skill_id)
                    if not skill:
                        continue

                    # 檢查技能等待回合限制
                    wait_round = skill.get("waitRound", 0)
                    if wait_round > 0 and current_round < wait_round:
                        continue

                    multiplier = skill["multiplier"] + (level - 1) * skill.get("multiplierperlvl", 0)
                    skill_type = skill.get("type", "atk")

                    if skill_type == "heal":
                        if user_hp >= user_battle_stats["hp"]:
                            continue
                        heal = int(user_battle_stats["hp"] * 0.1 * multiplier)
                        old_hp = user_hp
                        user_hp = min(user_hp + heal, user_battle_stats["hp"])
                        round_log.append(f"你使用 {skill['name']} 回復了 {user_hp - old_hp} 點生命值（目前 HP：{user_hp}/{user_battle_stats['hp']}）")

                    elif skill_type == "buff":
                        buff = {
                            "name": skill["name"],
                            "description": skill["description"],
                            "multiplier": skill.get("multiplier", 1.0),  # ✅ 修正：直接從技能獲取
                            "effectType": skill.get("effectType", "attack"),
                            "round": skill.get("round", 3)
                        }
                        add_or_refresh_buff(user_buffs, buff)
                        round_log.append(f"你施放了 {buff['name']} ，下回合將獲得強化")

                    elif skill_type == "debuff":
                        if calculate_hit(user_battle_stats["accuracy"], monster["stats"].get("evade", 0), user_battle_stats["luck"]):
                            debuff = {
                                "name": skill["name"],
                                "description": skill["description"],
                                "multiplier": skill.get("multiplier", 1.0),  # ✅ 修正：直接從技能獲取
                                "effectType": skill.get("effectType", "attack"),
                                "round": skill.get("round", 3)
                            }
                            add_or_refresh_debuff(mon_buffs, debuff)
                            round_log.append(f"你對 {monster['name']} 施放了 {debuff['name']} ，造成減益效果")
                        else:
                            round_log.append(f"你對 {monster['name']} 施放 {skill['name']} 但未命中")

                    # 新增玩家專用技能類型
                    elif skill_type == "debuff_atk":
                        target_invincible = mon_invincible > 0
                        if calculate_hit(user_battle_stats["accuracy"] * user_stats_mod["accuracy"],
                                         monster["stats"]["evade"] * mon_stats_mod["evade"],
                                         user_battle_stats["luck"]):
                            ele_mod = get_element_multiplier(skill.get("element", []), monster.get("element", []))
                            atk = user_battle_stats["attack"] * user_stats_mod["attack"]
                            shield = monster["stats"]["shield"] * mon_stats_mod["shield"]
                            penetrate = user_battle_stats.get("penetrate", 0)
                            dmg = calculate_damage(atk, multiplier, user_battle_stats.get("other_bonus", 0), shield, penetrate)
                            dmg = round(dmg * user_level_mod * ele_mod * user_stats_mod["all_damage"])
                            
                            if target_invincible:
                                dmg = 0
                            else:
                                # 處理傷害累積盾
                                if mon_damage_shield:
                                    mon_damage_shield["accumulated_damage"] += dmg
                                    if mon_damage_shield["accumulated_damage"] >= mon_damage_shield["threshold"]:
                                        round_log.append(f"你對 {monster['name']} 造成足夠傷害，阻止了其大招發動")
                                        mon_damage_shield = None
                                        
                            mon_hp = max(mon_hp - dmg, 0)
                            round_log.append(f"你使用 {skill['name']} 對 {monster['name']} 造成 {dmg} 傷害（對方 HP：{mon_hp}/{monster['stats']['hp']}）")
                            
                            # 處理負面效果
                            debuff_info = skill.get("debuff", {})
                            if debuff_info and calculate_hit(user_battle_stats["accuracy"] * user_stats_mod["accuracy"],
                                                             monster["stats"]["evade"] * mon_stats_mod["evade"],
                                                             user_battle_stats["luck"]) and random.random() <= debuff_info.get("hit_chance", 1.0):
                                debuff = {
                                    "name": debuff_info["name"],
                                    "description": debuff_info["description"],
                                    "multiplier": debuff_info["multiplier"],
                                    "effectType": debuff_info["effectType"],
                                    "round": debuff_info["round"]
                                }
                                add_or_refresh_debuff(mon_buffs, debuff)
                                round_log.append(f"你對 {monster['name']} 額外造成 {debuff['name']} 效果 {debuff['round']} 回合，{debuff['description']}")
                        else:
                            round_log.append(f"你使用 {skill['name']} 但未命中")
                            
                    elif skill_type == "dot_atk":
                        target_invincible = mon_invincible > 0
                        if calculate_hit(user_battle_stats["accuracy"] * user_stats_mod["accuracy"],
                                         monster["stats"]["evade"] * mon_stats_mod["evade"],
                                         user_battle_stats["luck"]):
                            ele_mod = get_element_multiplier(skill.get("element", []), monster.get("element", []))
                            atk = user_battle_stats["attack"] * user_stats_mod["attack"]
                            shield = monster["stats"]["shield"] * mon_stats_mod["shield"]
                            penetrate = user_battle_stats.get("penetrate", 0)
                            dmg = calculate_damage(atk, multiplier, user_battle_stats.get("other_bonus", 0), shield, penetrate)
                            dmg = round(dmg * user_level_mod * ele_mod * user_stats_mod["all_damage"])
                            
                            if target_invincible:
                                dmg = 0
                            else:
                                # 處理傷害累積盾
                                if mon_damage_shield:
                                    mon_damage_shield["accumulated_damage"] += dmg
                                    if mon_damage_shield["accumulated_damage"] >= mon_damage_shield["threshold"]:
                                        round_log.append(f"你對 {monster['name']} 造成足夠傷害，阻止了其大招發動")
                                        mon_damage_shield = None
                                        
                            mon_hp = max(mon_hp - dmg, 0)
                            round_log.append(f"你使用 {skill['name']} 對 {monster['name']} 造成 {dmg} 傷害（對方 HP：{mon_hp}/{monster['stats']['hp']}）")
                            
                            # 處理持續傷害
                            dot_info = skill.get("dot", {})
                            if dot_info and random.random() <= dot_info.get("hit_chance", 1.0):
                                # 計算隨等級提升的DOT傷害
                                base_dot_damage = dot_info["damage_per_turn"]
                                dot_damage_per_level = dot_info.get("damage_per_level", 0)
                                current_dot_damage = base_dot_damage + (level - 1) * dot_damage_per_level
                                
                                dot_effect = {
                                    "name": dot_info["name"],
                                    "damage_per_turn": int(current_dot_damage),
                                    "round": dot_info["round"]
                                }
                                add_dot_effect(mon_dot_effects, dot_effect)
                                round_log.append(f"{monster['name']} 陷入 {dot_effect['name']}狀態 持續 {dot_effect['round']} 回合")
                        else:
                            round_log.append(f"你使用 {skill['name']} 但未命中")
                            
                    elif skill_type == "invincible":
                        user_invincible = skill.get("round", 2)
                        round_log.append(f"你施放 {skill['name']} ， {user_invincible} 回合內將免疫所有傷害")
                        
                    elif skill_type == "damage_shield":
                        user_damage_shield = {
                            "rounds": skill.get("shield_rounds", 3),
                            "threshold": skill.get("damage_threshold", 150),
                            "accumulated_damage": 0,
                            "ultimate": skill.get("ultimate_skill", {"name": "元素爆發", "damage": 500})
                        }
                        round_log.append(f"你施放 {skill['name']} ， {user_damage_shield['rounds']} 回合內若敵人沒有造成 {user_damage_shield['threshold']} 傷害將會「{user_damage_shield['ultimate']['name']}」")

                    elif skill_type == "atk":
                        target_invincible = mon_invincible > 0
                        # 檢查是否必定命中（全屬性攻擊或特殊標記）
                        is_guaranteed_hit = (skill.get("element", []) == ["all"]) or skill.get("guaranteed_hit", False)
                        
                        if is_guaranteed_hit or calculate_hit(user_battle_stats["accuracy"] * user_stats_mod["accuracy"],
                                         monster["stats"]["evade"] * mon_stats_mod["evade"],
                                         user_battle_stats["luck"]):
                            ele_mod = get_element_multiplier(skill.get("element", []), monster.get("element", []))
                            atk = user_battle_stats["attack"] * user_stats_mod["attack"]
                            shield = monster["stats"]["shield"] * mon_stats_mod["shield"]
                            penetrate = user_battle_stats.get("penetrate", 0)
                            dmg = calculate_damage(atk, multiplier, user_battle_stats.get("other_bonus", 0), shield, penetrate)
                            dmg = round(dmg * user_level_mod * ele_mod * user_stats_mod["all_damage"])
                            
                            if target_invincible:
                                dmg = 0
                            else:
                                # 處理傷害累積盾
                                if mon_damage_shield:
                                    mon_damage_shield["accumulated_damage"] += dmg
                                    if mon_damage_shield["accumulated_damage"] >= mon_damage_shield["threshold"]:
                                        round_log.append(f"你對 {monster['name']} 造成足夠傷害，阻止了其大招發動")
                                        mon_damage_shield = None
                                
                            mon_hp = max(mon_hp - dmg, 0)
                            hit_message = f"你使用 {skill['name']} 對 {monster['name']} 造成 {dmg} 傷害（對方 HP：{mon_hp}/{monster['stats']['hp']}）"
                            if is_guaranteed_hit:
                                hit_message += " 【必定命中】"
                            round_log.append(hit_message)
                        else:
                            round_log.append(f"你使用 {skill['name']} 但未命中")

                    player_skill_cd[skill_id] = skill.get("cd", 0) + 1
                    any_skill_used = True

                if not any_skill_used:
                    target_invincible = mon_invincible > 0
                    if calculate_hit(user_battle_stats["accuracy"] * user_stats_mod["accuracy"],
                                     monster["stats"]["evade"] * mon_stats_mod["evade"],
                                     user_battle_stats["luck"]):
                        atk = user_battle_stats["attack"] * user_stats_mod["attack"]
                        shield = monster["stats"]["shield"] * mon_stats_mod["shield"]
                        penetrate = user_battle_stats.get("penetrate", 0)
                        dmg = calculate_damage(atk, 1.0, user_battle_stats.get("other_bonus", 0), shield, penetrate)
                        dmg = round(dmg * user_level_mod * user_stats_mod["all_damage"])
                        
                        if target_invincible:
                            dmg = 0
                        else:
                            # 處理傷害累積盾
                            if mon_damage_shield:
                                mon_damage_shield["accumulated_damage"] += dmg
                                if mon_damage_shield["accumulated_damage"] >= mon_damage_shield["threshold"]:
                                    round_log.append(f"你對 {monster['name']} 造成足夠傷害，阻止了其大招發動")
                                    mon_damage_shield = None
                            
                        mon_hp = max(mon_hp - dmg, 0)
                        round_log.append(f"你使用 普通攻擊 對 {monster['name']} 造成 {dmg} 傷害（對方 HP：{mon_hp}/{monster['stats']['hp']}）")
                    else:
                        round_log.append("你使用 普通攻擊 但未命中")
            else:  # 怪物回合
                for sid in monster_skill_cd:
                    if monster_skill_cd[sid] > 0:
                        monster_skill_cd[sid] -= 1

                skill = pick_monster_skill(monster.get("skills", []), monster_skill_cd)

                if skill.get("id") != "basic_attack":
                    monster_skill_cd[skill["id"]] = skill.get("cd", 0) + 1

                skill_type = skill.get("type", "atk")

                
                
                if skill_type == "heal":
                    heal = int(monster["stats"]["hp"] * 0.1 * skill["multiplier"])
                    old_hp = mon_hp
                    mon_hp = min(mon_hp + heal, monster["stats"]["hp"])
                    round_log.append(f"{monster['name']} 使用 {skill['description']} 回復了 {mon_hp - old_hp} 點生命值（目前 HP：{mon_hp}/{monster['stats']['hp']}）")

                elif skill_type == "bondage":
                    if calculate_hit(monster["stats"]["accuracy"] * mon_stats_mod["accuracy"],
                                     user_battle_stats["evade"] * user_stats_mod["evade"],
                                     monster["stats"]["luck"]):
                        bondage_round += skill.get("round", 1)
                        round_log.append(f"{monster['name']} 對你施放了 {skill['description']} ， {bondage_round} 回合內無法行動")
                    else:
                        skill_name = skill.get("buffInfo", {}).get("buffName", "未知技能")
                        round_log.append(f"{monster['name']} 對你施放 {skill['description']} 但未命中")

                elif skill_type == "combo_atk":
                    combo_rounds = skill.get("combo", 1)  # 幾連打
                    for i in range(combo_rounds):
                        target_invincible = user_invincible > 0
                        # 檢查是否必定命中（全屬性攻擊）
                        is_guaranteed_hit = skill.get("element", []) == ["all"]
                        
                        if is_guaranteed_hit or calculate_hit(monster["stats"]["accuracy"] * mon_stats_mod["accuracy"],
                                         user_battle_stats["evade"] * user_stats_mod["evade"],
                                         monster["stats"]["luck"]):
                            ele_mod = get_element_multiplier(skill.get("element", []), ["none"])
                            atk = monster["stats"]["attack"] * mon_stats_mod["attack"]
                            shield = user_battle_stats["shield"] * user_stats_mod["shield"]
                            mon_penetrate = monster["stats"].get("penetrate", 0)
                            dmg = calculate_damage(atk, skill["multiplier"], monster["stats"].get("other_bonus", 0), shield, mon_penetrate)
                            dmg = round(dmg * mon_level_mod * ele_mod * mon_stats_mod["all_damage"])
                            
                            if target_invincible:
                                dmg = 0
                            else:
                                # 處理傷害累積盾
                                if user_damage_shield:
                                    user_damage_shield["accumulated_damage"] += dmg
                                    if user_damage_shield["accumulated_damage"] >= user_damage_shield["threshold"]:
                                        round_log.append(f"{monster['name']} 的攻擊阻止了你的大招發動")
                                        user_damage_shield = None
                                        
                            user_hp = max(user_hp - dmg, 0)
                            hit_message = f"{monster['name']} 使用 {skill['description']} 對你造成 {dmg} 傷害（目前 HP：{user_hp}/{user_battle_stats['hp']}）"
                            if is_guaranteed_hit:
                                hit_message += " 【必定命中】"
                            round_log.append(hit_message)
                        else:
                            round_log.append(f"{monster['name']} 攻擊未命中")
                
                elif skill_type == "buff":
                    buff = {
                        "name": skill.get("buffInfo", {}).get("buffName", "未知"),
                        "description": skill["description"],
                        "multiplier": skill.get("buffInfo", {}).get("buffMultiplier", 1.0),
                        "effectType": skill.get("buffInfo", {}).get("effectType", "attack"),
                        "round": skill.get("buffInfo", {}).get("round", 3)
                    }
                    add_or_refresh_buff(mon_buffs, buff)
                    round_log.append(f"{monster['name']} 施放了 {buff['name']} ，{buff['description']}")
                
                elif skill_type == "debuff":
                    if calculate_hit(monster["stats"]["accuracy"] * mon_stats_mod["accuracy"],
                                     user_battle_stats["evade"] * user_stats_mod["evade"],
                                     monster["stats"]["luck"]):
                        debuff = {
                            "name": skill.get("buffInfo", {}).get("buffName", "未知"),
                            "description": skill["description"],
                            "multiplier": skill.get("buffInfo", {}).get("buffMultiplier", 1.0),
                            "effectType": skill.get("buffInfo", {}).get("effectType", "attack"),
                            "round": skill.get("buffInfo", {}).get("round", 3)
                        }
                        add_or_refresh_debuff(user_buffs, debuff)
                        round_log.append(f"{monster['name']} 對你施放了 {debuff['name']} ，{debuff['description']}")
                    else:
                        skill_name = skill.get("buffInfo", {}).get("buffName", "未知技能")
                        round_log.append(f"{monster['name']} 對你施放 {skill_name} 但未命中")
                        
                elif skill_type == "debuff_atk":
                    target_invincible = user_invincible > 0
                    # 檢查是否必定命中（全屬性攻擊）
                    is_guaranteed_hit = skill.get("element", []) == ["all"]
                    
                    if is_guaranteed_hit or calculate_hit(monster["stats"]["accuracy"] * mon_stats_mod["accuracy"],
                                     user_battle_stats["evade"] * user_stats_mod["evade"],
                                     monster["stats"]["luck"]):
                        ele_mod = get_element_multiplier(skill.get("element", []), ["none"])
                        atk = monster["stats"]["attack"] * mon_stats_mod["attack"]
                        shield = user_battle_stats["shield"] * user_stats_mod["shield"]
                        mon_penetrate = monster["stats"].get("penetrate", 0)
                        dmg = calculate_damage(atk, skill["multiplier"], monster["stats"].get("other_bonus", 0), shield, mon_penetrate)
                        dmg = round(dmg * mon_level_mod * ele_mod * mon_stats_mod["all_damage"])
                        
                        if target_invincible:
                            dmg = 0
                        else:
                            # 處理傷害累積盾
                            if user_damage_shield:
                                user_damage_shield["accumulated_damage"] += dmg
                                if user_damage_shield["accumulated_damage"] >= user_damage_shield["threshold"]:
                                    round_log.append(f"{monster['name']} 的攻擊阻止了你的大招發動")
                                    user_damage_shield = None
                                    
                        user_hp = max(user_hp - dmg, 0)
                        hit_message = f"{monster['name']} 使用 {skill['description']} 對你造成 {dmg} 傷害（目前 HP：{user_hp}/{user_battle_stats['hp']}）"
                        if is_guaranteed_hit:
                            hit_message += " 【必定命中】"
                        round_log.append(hit_message)
                        
                        # 處理負面效果
                        debuff_info = skill.get("debuff", {})
                        if debuff_info and (is_guaranteed_hit or calculate_hit(monster["stats"]["accuracy"] * mon_stats_mod["accuracy"],
                                                         user_battle_stats["evade"] * user_stats_mod["evade"],
                                                         monster["stats"]["luck"])) and random.random() <= debuff_info.get("hit_chance", 1.0):
                            debuff = {
                                "name": debuff_info["name"],
                                "description": debuff_info["description"],
                                "multiplier": debuff_info["multiplier"],
                                "effectType": debuff_info["effectType"],
                                "round": debuff_info["round"]
                            }
                            add_or_refresh_debuff(user_buffs, debuff)
                            round_log.append(f"額外對你造成 {debuff['name']} 效果 {debuff['round']} 回合，{debuff['description']}")
                    else:
                        round_log.append(f"{monster['name']} 攻擊未命中")
                        
                elif skill_type == "dot_atk":
                    target_invincible = user_invincible > 0
                    # 檢查是否必定命中（全屬性攻擊）
                    is_guaranteed_hit = skill.get("element", []) == ["all"]
                    
                    if is_guaranteed_hit or calculate_hit(monster["stats"]["accuracy"] * mon_stats_mod["accuracy"],
                                     user_battle_stats["evade"] * user_stats_mod["evade"],
                                     monster["stats"]["luck"]):
                        ele_mod = get_element_multiplier(skill.get("element", []), ["none"])
                        atk = monster["stats"]["attack"] * mon_stats_mod["attack"]
                        shield = user_battle_stats["shield"] * user_stats_mod["shield"]
                        mon_penetrate = monster["stats"].get("penetrate", 0)
                        dmg = calculate_damage(atk, skill["multiplier"], monster["stats"].get("other_bonus", 0), shield, mon_penetrate)
                        dmg = round(dmg * mon_level_mod * ele_mod * mon_stats_mod["all_damage"])
                        
                        if target_invincible:
                            dmg = 0
                        else:
                            # 處理傷害累積盾
                            if user_damage_shield:
                                user_damage_shield["accumulated_damage"] += dmg
                                if user_damage_shield["accumulated_damage"] >= user_damage_shield["threshold"]:
                                    round_log.append(f"{monster['name']} 的攻擊阻止了你的大招發動")
                                    user_damage_shield = None
                                    
                        user_hp = max(user_hp - dmg, 0)
                        hit_message = f"{monster['name']} 使用 {skill['description']} 對你造成 {dmg} 傷害（目前 HP：{user_hp}/{user_battle_stats['hp']}）"
                        if is_guaranteed_hit:
                            hit_message += " 【必定命中】"
                        round_log.append(hit_message)
                        
                        # 處理持續傷害
                        dot_info = skill.get("dot", {})
                        if dot_info and (is_guaranteed_hit or random.random() <= dot_info.get("hit_chance", 1.0)):
                            dot_effect = {
                                "name": dot_info["name"],
                                "damage_per_turn": dot_info["damage_per_turn"],
                                "round": dot_info["round"]
                            }
                            add_dot_effect(user_dot_effects, dot_effect)
                            round_log.append(f"你陷入 {dot_effect['name']}狀態 持續 {dot_effect['round']} 回合")
                    else:
                        round_log.append(f"{monster['name']} 攻擊未命中")
                        
                elif skill_type == "invincible":
                    mon_invincible = skill["round"]
                    round_log.append(f"{monster['name']} 施放 {skill['description']} ， {skill['round']} 回合內將免疫所有傷害")
                    
                elif skill_type == "damage_shield":
                    mon_damage_shield = {
                        "rounds": skill["shield_rounds"],
                        "threshold": skill["damage_threshold"],
                        "accumulated_damage": 0,
                        "ultimate": skill["ultimate_skill"]
                    }
                    round_log.append(f"{monster['name']} 施放 {skill['description']} ， {skill['shield_rounds']} 回合內若沒有造成 {skill['damage_threshold']} 傷害將會「{skill['ultimate_skill']['name']}」")
                    
                elif skill_type == "atk":
                    target_invincible = user_invincible > 0
                    # 檢查是否必定命中（全屬性攻擊）
                    is_guaranteed_hit = skill.get("element", []) == ["all"]
                    
                    if is_guaranteed_hit or calculate_hit(monster["stats"]["accuracy"] * mon_stats_mod["accuracy"],
                                     user_battle_stats["evade"] * user_stats_mod["evade"],
                                     monster["stats"]["luck"]):
                        ele_mod = get_element_multiplier(skill.get("element", []), ["none"])
                        atk = monster["stats"]["attack"] * mon_stats_mod["attack"]
                        shield = user_battle_stats["shield"] * user_stats_mod["shield"]
                        mon_penetrate = monster["stats"].get("penetrate", 0)
                        dmg = calculate_damage(atk, skill["multiplier"], monster["stats"].get("other_bonus", 0), shield, mon_penetrate)
                        dmg = round(dmg * mon_level_mod * ele_mod * mon_stats_mod["all_damage"])
                        
                        if target_invincible:
                            dmg = 0
                        else:
                            # 處理傷害累積盾
                            if user_damage_shield:
                                user_damage_shield["accumulated_damage"] += dmg
                                if user_damage_shield["accumulated_damage"] >= user_damage_shield["threshold"]:
                                    round_log.append(f"{monster['name']} 的攻擊阻止了你的大招發動")
                                    user_damage_shield = None
                                    
                        user_hp = max(user_hp - dmg, 0)
                        hit_message = f"{monster['name']} 使用 {skill['description']} 對你造成 {dmg} 傷害（目前 HP：{user_hp}/{user_battle_stats['hp']}）"
                        if is_guaranteed_hit:
                            hit_message += " 【必定命中】"
                        round_log.append(hit_message)
                    else:
                        round_log.append(f"{monster['name']} 攻擊未命中")
        round_log.extend(buff_log_user)
        round_log.extend(buff_log_mon)
        log.append({"round": current_round, "actions": round_log})
        if user_hp <= 0 or mon_hp <= 0:
            break

    outcome = "win" if user_hp > 0 and mon_hp <= 0 else "lose"
    rewards = {}
    
    # ✅ 更新最後戰鬥時間戳（無論勝負）
    battle_completion_time = round(time.time(), 2)  # 保留2位小數精度
    user["last_battle"] = battle_completion_time
    
    if outcome == "win":
        base_exp = monster["exp"]
        
        # 💎 新增：經驗值幸運加成邏輯（與掉落物相同）
        # 每點幸運 +0.2% 額外經驗機率，上限25%
        exp_extra_chance = min(user_battle_stats["luck"] * 0.002, 0.25)
        
        # 基礎經驗值
        final_exp = base_exp
        exp_luck_bonus = 0
        
        # 🎲 判定是否獲得額外經驗（雙倍經驗）
        if random.random() <= exp_extra_chance:
            exp_luck_bonus = base_exp
            final_exp += exp_luck_bonus
        
        user["exp"] += final_exp
        leveled = check_level_up(user)
        
        # 處理掉落物（修改為回傳實際掉落資訊）
        drop_result = apply_drops(db, user["user_id"], monster["drops"], user_battle_stats["luck"])
        
        rewards = {
            "exp": final_exp,
            "base_exp": base_exp,
            "exp_luck_bonus": exp_luck_bonus,
            "leveled_up": leveled,
            "drops": monster["drops"],  # 原始掉落表
            "actual_drops": drop_result["items"],  # 實際獲得的物品總數
            "round_1_bonus": drop_result["round_1_bonus"],  # 第一輪額外獲得
            "round_2_bonus": drop_result["round_2_bonus"],  # 第二輪額外獲得  
            "round_3_bonus": drop_result["round_3_bonus"],  # 第三輪額外獲得
            # 向下相容：保留原有的 drop_luck_bonus 格式
            "drop_luck_bonus": {
                **drop_result["round_2_bonus"], 
                **drop_result["round_3_bonus"]
            }
        }

    return {
        "result": outcome,
        "battle_log": log,
        "user": user,
        "rewards": rewards if outcome == "win" else None
    }
