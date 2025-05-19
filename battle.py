import random
from firebase_admin import firestore

def calculate_hit(attacker_acc, defender_evade, attacker_luck):
    # 命中率 - 迴避率 + 運氣補正，每點 luck +1%
    hit_chance = attacker_acc - defender_evade + (attacker_luck * 0.01)
    hit_chance = min(max(hit_chance, 0.05), 0.99)
    return random.random() < hit_chance

def calculate_damage(base_atk, skill_multiplier, bonus, shield):
    raw = base_atk * skill_multiplier * (1 + bonus)
    reduction = min(shield * 0.001, 0.99)
    return int(raw * (1 - reduction))

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

def check_level_up(user):
    level = user["level"]
    current_exp = user["exp"]
    required_exp = level * 5  # 升級門檻：每級 *5
    leveled_up = False

    while current_exp >= required_exp:
        current_exp -= required_exp
        level += 1
        user["stat_points"] += 5
        user["skill_points"] += 3
        required_exp = level * 5
        leveled_up = True

    user["level"] = level
    user["exp"] = current_exp
    return leveled_up

def simulate_battle(user, monster):
    log = []
    user_hp = user["base_stats"]["hp"]
    mon_hp = monster["stats"]["hp"]
    db = firestore.client()

    while user_hp > 0 and mon_hp > 0:
        # 玩家攻擊
        if calculate_hit(user["base_stats"]["accuracy"], monster["stats"]["evade"], user["base_stats"]["luck"]):
            dmg = calculate_damage(user["base_stats"]["attack"], 1.0, user["buffs"]["phys_bonus"], monster["stats"]["shield"])
            mon_hp -= dmg
            log.append(f"你對 {monster['name']} 造成 {dmg} 傷害")
        else:
            log.append("你攻擊未命中")

        if mon_hp <= 0:
            break

        # 怪物攻擊
        skill = random.choice(monster["skills"]) if monster.get("skills") else {"multiplier": 1.0, "description": "普通攻擊"}
        if calculate_hit(monster["stats"]["accuracy"], user["base_stats"]["evade"], monster["stats"]["luck"]):
            dmg = calculate_damage(monster["stats"]["attack"], skill["multiplier"], monster["stats"]["phys_bonus"], user["base_stats"]["shield"])
            user_hp -= dmg
            log.append(f"{monster['name']} 使用 {skill['description']} 對你造成 {dmg} 傷害")
        else:
            log.append(f"{monster['name']} 攻擊失敗")

    # 結果結算
    outcome = "win" if user_hp > 0 else "lose"
    rewards = {}

    if outcome == "win":
        user["exp"] += monster["exp"]
        leveled = check_level_up(user)
        apply_drops(db, user["nickname"], monster["drops"])
        rewards["exp"] = monster["exp"]
        rewards["leveled_up"] = leveled
        rewards["drops"] = monster["drops"]

    return {
        "result": outcome,
        "battle_log": log,
        "user": user,
        "rewards": rewards if outcome == "win" else None
    }
