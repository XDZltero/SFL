import random
from firebase_admin import firestore
import json

# 命中計算
def calculate_hit(attacker_acc, defender_evade, attacker_luck):
    # 命中率 - 迴避率 + 運氣補正，每點 luck +1%
    hit_chance = attacker_acc - defender_evade + (attacker_luck * 0.01)
    hit_chance = min(max(hit_chance, 0.05), 0.99)
    return random.random() < hit_chance

# 傷害計算
def calculate_damage(base_atk, skill_multiplier, bonus, shield):
    raw = base_atk * skill_multiplier * (1 + bonus)
    reduction = min(shield * 0.001, 0.99)
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

def apply_drops(db, user_id, drops):
    ref = get_user_item_ref(db, user_id)
    snap = ref.get()
    current = snap.to_dict() if snap.exists else {"id": user_id, "items": {}}

    for drop in drops:
        if random.random() <= drop["rate"]:
            item_id = drop["id"]
            qty = drop["value"]
            current["items"][item_id] = current["items"].get(item_id, 0) + qty

    ref.set(current)

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

# 確認是否升級
def check_level_up(user):
    with open("parameter/level_exp.json", "r", encoding="utf-8") as f:
        level_table = json.load(f)

    level = user["level"]
    exp = user["exp"]
    leveled = False

    while str(level) in level_table and exp >= level_table[str(level)]:
        exp -= level_table[str(level)]
        level += 1
        user["stat_points"] += 5
        user["skill_points"] += 3
        leveled = True

    user["level"] = level
    user["exp"] = exp
    return leveled

# 怪物選擇技能
def pick_monster_skill(skills):
    r = random.random()
    total = 0
    for s in skills:
        total += s.get("chance", 0)
        if r < total:
            return s
    return {"multiplier": 1.0, "description": "普通攻擊"}

# 戰鬥
def simulate_battle(user, monster):
    log = []
    db = firestore.client()

    user_hp = user["base_stats"]["hp"]
    mon_hp = monster["stats"]["hp"]

    user_speed = user["base_stats"].get("atk_speed", 100)
    mon_speed = monster["stats"].get("atk_speed", 100)

    while user_hp > 0 and mon_hp > 0:
        user_turns = max(1, round(user_speed / mon_speed))
        mon_turns = max(1, round(mon_speed / user_speed))

        # 攻速比較
        action_order = []
        if user_speed >= mon_speed:
            action_order.extend(["user"] * user_turns + ["mon"] * mon_turns)
        else:
            action_order.extend(["mon"] * mon_turns + ["user"] * user_turns)

        # 等差增減傷
        user_level_mod = level_damage_modifier(user["level"], monster["level"])
        monster_level_mod = level_damage_modifier(monster["level"], user["level"])

        for actor in action_order:
            if user_hp <= 0 or mon_hp <= 0:
                break

            if actor == "user":
                for skill_id, level in user.get("skills", {}).items():
                    if mon_hp <= 0:
                        break

                    skill_doc = db.collection("skills").document(skill_id).get()
                    if not skill_doc.exists:
                        continue
                    skill = skill_doc.to_dict()
                    multiplier = skill["multiplier"] + (level - 1) * skill.get("multiplierperlvl", 0)

                    if calculate_hit(user["base_stats"]["accuracy"], monster["stats"]["evade"], user["base_stats"]["luck"]):
                        
                        dmg = calculate_damage(user["base_stats"]["attack"], multiplier, user["buffs"]["phys_bonus"], monster["stats"]["shield"])

                        # 屬性克制
                        ele_mod = get_element_multiplier(skill.get("element", []), monster.get("element", []))
                        
                        dmg = round(dmg * user_level_mod * ele_mod) # 傷害 * 等差增減傷 * 屬性克制
                        mon_hp -= dmg
                        log.append(f"你使用 {skill['name']} 對 {monster['name']} 造成 {dmg} 傷害")
                    else:
                        log.append(f"你使用 {skill['name']} 但未命中")
            else:
                skill = pick_monster_skill(monster.get("skills", []))
                if calculate_hit(monster["stats"]["accuracy"], user["base_stats"]["evade"], monster["stats"]["luck"]):
                    
                    dmg = calculate_damage(monster["stats"]["attack"], skill["multiplier"], monster["stats"].get("phys_bonus", 0), user["base_stats"]["shield"])

                    # 屬性克制
                    ele_mod = get_element_multiplier(skill.get("element", []), ["none"])
                    
                    dmg = round(dmg * monster_level_mod * ele_mod) # 傷害 * 等差增減傷 * 屬性克制
                    
                    user_hp -= dmg
                    log.append(f"{monster['name']} 使用 {skill['description']} 對你造成 {dmg} 傷害")
                else:
                    log.append(f"{monster['name']} 攻擊未命中")

    outcome = "win" if user_hp > 0 else "lose"
    rewards = {}

    if outcome == "win":
        user["exp"] += monster["exp"]
        leveled = check_level_up(user)
        apply_drops(db, user["user_id"], monster["drops"])
        rewards["exp"] = monster["exp"]
        rewards["leveled_up"] = leveled
        rewards["drops"] = monster["drops"]

    return {
        "result": outcome,
        "battle_log": log,
        "user": user,
        "rewards": rewards if outcome == "win" else None
    }
