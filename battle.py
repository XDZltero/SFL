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
    for buff in buffs:
        if buff["round"] > 0:
            effect = buff.get("effectType", "")
            multiplier = buff.get("multiplier", 1.0)
            if effect in stats_mod:
                stats_mod[effect] *= multiplier

            buff["round"] -= 1
            if buff["round"] > 0:
                new_buffs.append(buff)
                msg = f"你身上的 {buff['name']} 效果剩下 {buff['round']} 回合" if is_user else f"{actor_name} 的 {buff['description']} 效果剩下 {buff['round']} 回合"
                temp_log.append(msg)
            else:
                msg = f"你身上的 {buff['name']} 效果已消失" if is_user else f"{actor_name} 施放的 {buff['description']} 效果已消失"
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

def simulate_battle(user, monster):
    log = []
    db = firestore.client()

    user_hp = user["base_stats"]["hp"]
    mon_hp = monster["stats"]["hp"]
    user_buffs = []
    mon_buffs = []

    turn_limit = 20 if monster.get("is_boss") else 10
    player_turns_used = 0
    
    user_stats_mod = init_stats_mod()
    mon_stats_mod = init_stats_mod()

    while user_hp > 0 and mon_hp > 0:
        if player_turns_used >= turn_limit:
            log.append(f"⚠️ 已超過回合上限（{turn_limit} 回合），戰鬥失敗")
            break
        
        # 計算出手
        user_stats_mod_preview = get_buff_stats_only(user_buffs)
        mon_stats_mod_preview = get_buff_stats_only(mon_buffs)
        
        user_speed = user["base_stats"]["atk_speed"] * user_stats_mod_preview["atk_speed"]
        mon_speed = monster["stats"]["atk_speed"] * mon_stats_mod_preview["atk_speed"]

        user_turns = max(1, round(user_speed / mon_speed))
        mon_turns = max(1, round(mon_speed / user_speed))

        action_order = []
        if user_speed >= mon_speed:
            action_order.extend(["user"] * user_turns + ["mon"] * mon_turns)
        else:
            action_order.extend(["mon"] * mon_turns + ["user"] * user_turns)

        user_level_mod = level_damage_modifier(user["level"], monster["level"])
        mon_level_mod = level_damage_modifier(monster["level"], user["level"])

        for actor in action_order:
            if user_hp <= 0 or mon_hp <= 0:
                break

            if actor == "user":
                player_turns_used += 1

                if player_turns_used > turn_limit:
                    log.append(f"⚠️ 已超過回合上限（{turn_limit} 回合），戰鬥失敗")
                    user_hp = 0  # 強制失敗
                    break
                
                # 確認玩家身上 buff
                user_stats_mod, user_buffs, buff_log = apply_buffs(user_buffs, user["base_stats"], log, True, "")

                for skill_id, level in user.get("skills", {}).items():
                    skill_doc = db.collection("skills").document(skill_id).get()
                    if not skill_doc.exists:
                        continue
                    skill = skill_doc.to_dict()
                    multiplier = skill["multiplier"] + (level - 1) * skill.get("multiplierperlvl", 0)
                    skill_type = skill.get("type", "atk")

                    if skill_type == "heal":
                        heal = int(user["base_stats"]["hp"] * 0.1 * multiplier)
                        old_hp = user_hp
                        user_hp = min(user_hp + heal, user["base_stats"]["hp"])
                        actual_heal = user_hp - old_hp
                        log.append(f"你使用 {skill['name']} 回復了 {actual_heal} 點生命值（目前 HP：{user_hp}/{user['base_stats']['hp']}）")
                        break

                    elif skill_type == "buff":
                        buff = {
                            "name": skill["name"],
                            "description": skill["description"],
                            "multiplier": skill["multiplier"],
                            "effectType": skill.get("effectType", "attack"),
                            "round": skill.get("round", 3)
                        }
                        add_or_refresh_buff(user_buffs, buff)
                        log.append(f"你施放了 {buff['name']} ，自身獲得強化")
                        break

                    elif skill_type == "debuff":
                        if calculate_hit(user["base_stats"]["accuracy"], monster["stats"].get("evade", 0), user["base_stats"].get("luck", 0)):
                            debuff = {
                                "name": skill["name"],
                                "description": skill["description"],
                                "multiplier": skill["multiplier"],
                                "effectType": skill.get("effectType", "attack"),
                                "round": skill.get("round", 3)
                            }
                            add_or_refresh_debuff(mon_buffs, debuff)
                            log.append(f"你對 {monster['name']} 施放了 {debuff['name']} ，造成減益效果")
                        else:
                            log.append(f"你對 {monster['name']} 施放 {skill['name']} 但未命中")
                        break

                    elif skill_type == "atk":
                        if calculate_hit(user["base_stats"].get("accuracy", 1.0) * user_stats_mod.get("accuracy", 1.0), monster["stats"].get("evade", 0) * mon_stats_mod.get("evade", 1.0), user["base_stats"].get("luck", 0)):
                            ele_mod = get_element_multiplier(skill.get("element", []), monster.get("element", []))
                            atk = user["base_stats"].get("attack", 0) * user_stats_mod.get("attack", 1.0)
                            shield = monster["stats"].get("shield", 0) * mon_stats_mod.get("shield", 1.0)
                            dmg = calculate_damage(atk, multiplier, user["buffs"].get("phys_bonus", 0), shield)
                            dmg = round(dmg * user_level_mod * ele_mod * user_stats_mod.get("all_damage", 1.0))
                            mon_hp -= dmg
                            mon_hp = max(mon_hp, 0)
                            log.append(f"你使用 {skill['name']} 對 {monster['name']} 造成 {dmg} 傷害（對方 HP：{mon_hp}/{monster['stats']['hp']}）")
                        else:
                            log.append(f"你使用 {skill['name']} 但未命中")
                log.extend(buff_log)

            else:
                
                # 確認怪物身上 buff
                mon_stats_mod, mon_buffs, buff_log = apply_buffs(mon_buffs, monster["stats"], log, False, monster["name"])
                
                
                skill = pick_monster_skill(monster.get("skills", []))
                skill_type = skill.get("type", "atk")

                if skill_type == "heal":
                    heal = int(monster["stats"]["hp"] * 0.1 * skill["multiplier"])
                    old_hp = mon_hp
                    mon_hp = min(mon_hp + heal, monster["stats"]["hp"])
                    actual_heal = mon_hp - old_hp
                    log.append(f"{monster['name']} 使用 {skill['description']} 回復了 {actual_heal} 點生命值（目前 HP：{mon_hp}/{monster['stats']['hp']}）")

                elif skill_type == "buff":
                    buff = {
                        "name": skill["description"],
                        "description": skill["description"],
                        "multiplier": skill["multiplier"],
                        "effectType": skill.get("effectType", "atk"),
                        "round": skill.get("round", 3)
                    }
                    add_or_refresh_buff(mon_buffs, buff)
                    log.append(f"{monster['name']} 施放了 {buff['description']} ，自身獲得強化")

                elif skill_type == "debuff":
                    if calculate_hit(monster["stats"].get("accuracy", 1.0) * mon_stats_mod.get("accuracy", 1.0), user["base_stats"].get("evade", 0) * user_stats_mod.get("evade", 1.0), monster["stats"].get("luck", 0)):
                        debuff = {
                            "name": skill["description"],
                            "description": skill["description"],
                            "multiplier": skill["multiplier"],
                            "effectType": skill.get("effectType", "atk"),
                            "round": skill.get("round", 3)
                        }
                        add_or_refresh_debuff(user_buffs, debuff)
                        log.append(f"{monster['name']} 對你施放了 {debuff['description']} ，造成減益效果")
                    else:
                        log.append(f"{monster['name']} 對你施放 {skill['description']} 但未命中")

                elif skill_type == "atk":
                    if calculate_hit(monster["stats"].get("accuracy", 1.0) * mon_stats_mod.get("accuracy", 1.0), user["base_stats"].get("evade", 0) * user_stats_mod.get("evade", 1.0), monster["stats"].get("luck", 0)):
                        ele_mod = get_element_multiplier(skill.get("element", []), ["none"])
                        atk = monster["stats"].get("attack", 0) * mon_stats_mod.get("attack", 1.0)
                        shield = user["base_stats"].get("shield", 0) * user_stats_mod.get("shield", 1.0)
                        dmg = calculate_damage(atk, skill["multiplier"], monster["stats"].get("phys_bonus", 0), shield)
                        dmg = round(dmg * mon_level_mod * ele_mod * mon_stats_mod.get("all_damage", 1.0))
                        user_hp -= dmg
                        user_hp = max(user_hp, 0)
                        log.append(f"{monster['name']} 使用 {skill['description']} 對你造成 {dmg} 傷害（目前 HP：{user_hp}/{user['base_stats']['hp']}）")
                    else:
                        log.append(f"{monster['name']} 攻擊未命中")
                log.extend(buff_log)

    outcome = "win" if user_hp > 0 and mon_hp <= 0 else "lose"
    rewards = {}

    if outcome == "win":
        user["exp"] += monster["exp"]
        leveled = check_level_up(user)
        apply_drops(db, user["user_id"], monster["drops"])
        rewards = {
            "exp": monster["exp"],
            "leveled_up": leveled,
            "drops": monster["drops"]
        }

    return {
        "result": outcome,
        "battle_log": log,
        "user": user,
        "rewards": rewards if outcome == "win" else None
    }


# 戰鬥
# def simulate_battle(user, monster):
    # log = []
    # db = firestore.client()

    # user_hp = user["base_stats"]["hp"]
    # mon_hp = monster["stats"]["hp"]

    # user_speed = user["base_stats"].get("atk_speed", 100)
    # mon_speed = monster["stats"].get("atk_speed", 100)

    # while user_hp > 0 and mon_hp > 0:
        # user_turns = max(1, round(user_speed / mon_speed))
        # mon_turns = max(1, round(mon_speed / user_speed))

        # # 攻速比較
        # action_order = []
        # if user_speed >= mon_speed:
            # action_order.extend(["user"] * user_turns + ["mon"] * mon_turns)
        # else:
            # action_order.extend(["mon"] * mon_turns + ["user"] * user_turns)

        # # 等差增減傷
        # user_level_mod = level_damage_modifier(user["level"], monster["level"])
        # monster_level_mod = level_damage_modifier(monster["level"], user["level"])

        # for actor in action_order:
            # if user_hp <= 0 or mon_hp <= 0:
                # break

            # if actor == "user":
                # for skill_id, level in user.get("skills", {}).items():
                    # if mon_hp <= 0:
                        # break

                    # skill_doc = db.collection("skills").document(skill_id).get()
                    # if not skill_doc.exists:
                        # continue
                    # skill = skill_doc.to_dict()
                    # multiplier = skill["multiplier"] + (level - 1) * skill.get("multiplierperlvl", 0)

                    # if calculate_hit(user["base_stats"]["accuracy"], monster["stats"]["evade"], user["base_stats"]["luck"]):
                        
                        # dmg = calculate_damage(user["base_stats"]["attack"], multiplier, user["buffs"]["phys_bonus"], monster["stats"]["shield"])

                        # # 屬性克制
                        # ele_mod = get_element_multiplier(skill.get("element", []), monster.get("element", []))
                        
                        # dmg = round(dmg * user_level_mod * ele_mod) # 傷害 * 等差增減傷 * 屬性克制
                        # mon_hp -= dmg
                        # log.append(f"你使用 {skill['name']} 對 {monster['name']} 造成 {dmg} 傷害")
                    # else:
                        # log.append(f"你使用 {skill['name']} 但未命中")
            # else:
                # skill = pick_monster_skill(monster.get("skills", []))
                # if calculate_hit(monster["stats"]["accuracy"], user["base_stats"]["evade"], monster["stats"]["luck"]):
                    
                    # dmg = calculate_damage(monster["stats"]["attack"], skill["multiplier"], monster["stats"].get("phys_bonus", 0), user["base_stats"]["shield"])

                    # # 屬性克制
                    # ele_mod = get_element_multiplier(skill.get("element", []), ["none"])
                    
                    # dmg = round(dmg * monster_level_mod * ele_mod) # 傷害 * 等差增減傷 * 屬性克制
                    
                    # user_hp -= dmg
                    # log.append(f"{monster['name']} 使用 {skill['description']} 對你造成 {dmg} 傷害")
                # else:
                    # log.append(f"{monster['name']} 攻擊未命中")

    # outcome = "win" if user_hp > 0 else "lose"
    # rewards = {}

    # if outcome == "win":
        # user["exp"] += monster["exp"]
        # leveled = check_level_up(user)
        # apply_drops(db, user["user_id"], monster["drops"])
        # rewards["exp"] = monster["exp"]
        # rewards["leveled_up"] = leveled
        # rewards["drops"] = monster["drops"]

    # return {
        # "result": outcome,
        # "battle_log": log,
        # "user": user,
        # "rewards": rewards if outcome == "win" else None
    # }
