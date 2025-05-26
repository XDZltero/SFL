import random
from firebase_admin import firestore
import json
import copy

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
                stats_mod[effect] *= multiplier

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

# 玩家攻擊以及傷害公式
def player_attack(user, monster, skill, multiplier, user_stats_mod, mon_stats_mod, user_level_mod, log):
    if calculate_hit(user["base_stats"].get("accuracy", 1.0) * user_stats_mod.get("accuracy", 1.0),
                     monster["stats"].get("evade", 0) * mon_stats_mod.get("evade", 1.0),
                     user["base_stats"].get("luck", 0)):
        ele_mod = get_element_multiplier(skill.get("element", []), monster.get("element", []))
        atk = user["base_stats"].get("attack", 0) * user_stats_mod.get("attack", 1.0)
        shield = monster["stats"].get("shield", 0) * mon_stats_mod.get("shield", 1.0)
        dmg = calculate_damage(atk, multiplier, user["buffs"].get("phys_bonus", 0), shield)
        dmg = round(dmg * user_level_mod * ele_mod * user_stats_mod.get("all_damage", 1.0))
        monster_hp = max(monster["stats"]["hp"] - dmg, 0)
        log.append(f"你使用 {skill['name']} 對 {monster['name']} 造成 {dmg} 傷害（對方 HP：{monster_hp}/{monster['stats']['hp']}）")
        return dmg
    else:
        log.append(f"你使用 {skill['name']} 但未命中")
        return 0




def simulate_battle(user, monster, user_skill_dict):
    log = []
    db = firestore.client()

    user_hp = user["base_stats"]["hp"]
    mon_hp = monster["stats"]["hp"]
    player_skill_cd = {k: 0 for k in user.get("skills", {})}
    monster_skill_cd = {s["id"]: 0 for s in monster.get("skills", [])}
    user_buffs = []
    mon_buffs = []

    turn_limit = 20 if monster.get("is_boss") else 10
    turns_used = 0

    user_stats_mod = init_stats_mod()
    mon_stats_mod = init_stats_mod()

    while user_hp > 0 and mon_hp > 0:
        turns_used += 1
        current_round = turns_used
        round_log = []  # 每回合初始化 log

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
                for sid in player_skill_cd:
                    if player_skill_cd[sid] > 0:
                        player_skill_cd[sid] -= 1

                user_stats_mod, user_buffs, buff_log = apply_buffs(user_buffs, user["base_stats"], log, True, "")
                

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

                    multiplier = skill["multiplier"] + (level - 1) * skill.get("multiplierperlvl", 0)
                    skill_type = skill.get("type", "atk")

                    if skill_type == "heal":

                        # 滿血不補
                        if user_hp >= user['base_stats']['hp']:
                            continue
                        
                        heal = int(user["base_stats"]["hp"] * 0.1 * multiplier)
                        old_hp = user_hp
                        user_hp = min(user_hp + heal, user["base_stats"]["hp"])
                        round_log.append(f"你使用 {skill['name']} 回復了 {user_hp - old_hp} 點生命值（目前 HP：{user_hp}/{user['base_stats']['hp']}）")

                    elif skill_type == "buff":
                        buff = {
                            "name": skill["name"],
                            "description": skill["description"],
                            "multiplier": skill["multiplier"],
                            "effectType": skill.get("effectType", "attack"),
                            "round": skill.get("round", 3)
                        }
                        add_or_refresh_buff(user_buffs, buff)
                        round_log.append(f"你施放了 {buff['name']} ，自身獲得強化")

                    elif skill_type == "debuff":
                        if calculate_hit(user["base_stats"]["accuracy"], monster["stats"].get("evade", 0), user["base_stats"]["luck"]):
                            debuff = {
                                "name": skill["name"],
                                "description": skill["description"],
                                "multiplier": skill["multiplier"],
                                "effectType": skill.get("effectType", "attack"),
                                "round": skill.get("round", 3)
                            }
                            add_or_refresh_debuff(mon_buffs, debuff)
                            round_log.append(f"你對 {monster['name']} 施放了 {debuff['name']} ，造成減益效果")
                        else:
                            round_log.append(f"你對 {monster['name']} 施放 {skill['name']} 但未命中")

                    elif skill_type == "atk":
                        if calculate_hit(user["base_stats"]["accuracy"] * user_stats_mod["accuracy"],
                                         monster["stats"]["evade"] * mon_stats_mod["evade"],
                                         user["base_stats"]["luck"]):
                            ele_mod = get_element_multiplier(skill.get("element", []), monster.get("element", []))
                            atk = user["base_stats"]["attack"] * user_stats_mod["attack"]
                            shield = monster["stats"]["shield"] * mon_stats_mod["shield"]
                            dmg = calculate_damage(atk, multiplier, user.get("other_bonus", 0), shield)
                            dmg = round(dmg * user_level_mod * ele_mod * user_stats_mod["all_damage"])
                            mon_hp = max(mon_hp - dmg, 0)
                            round_log.append(f"你使用 {skill['name']} 對 {monster['name']} 造成 {dmg} 傷害（對方 HP：{mon_hp}/{monster['stats']['hp']}）")
                        else:
                            round_log.append(f"你使用 {skill['name']} 但未命中")

                    player_skill_cd[skill_id] = skill.get("cd", 0) + 1
                    any_skill_used = True

                if not any_skill_used:
                    if calculate_hit(user["base_stats"]["accuracy"] * user_stats_mod["accuracy"],
                                     monster["stats"]["evade"] * mon_stats_mod["evade"],
                                     user["base_stats"]["luck"]):
                        atk = user["base_stats"]["attack"] * user_stats_mod["attack"]
                        shield = monster["stats"]["shield"] * mon_stats_mod["shield"]
                        dmg = calculate_damage(atk, 1.0, user.get("other_bonus", 0), shield)
                        dmg = round(dmg * user_level_mod * user_stats_mod["all_damage"])
                        mon_hp = max(mon_hp - dmg, 0)
                        round_log.append(f"你使用 普通攻擊 對 {monster['name']} 造成 {dmg} 傷害（對方 HP：{mon_hp}/{monster['stats']['hp']}）")
                    else:
                        round_log.append("你使用 普通攻擊 但未命中")
                round_log.extend(buff_log)

            else:
                for sid in monster_skill_cd:
                    if monster_skill_cd[sid] > 0:
                        monster_skill_cd[sid] -= 1

                mon_stats_mod, mon_buffs, buff_log = apply_buffs(mon_buffs, monster["stats"], log, False, monster["name"])
                skill = pick_monster_skill(monster.get("skills", []), monster_skill_cd)

                if skill.get("id") != "basic_attack":
                    monster_skill_cd[skill["id"]] = skill.get("cd", 0) + 1

                skill_type = skill.get("type", "atk")

                if skill_type == "heal":
                    heal = int(monster["stats"]["hp"] * 0.1 * skill["multiplier"])
                    old_hp = mon_hp
                    mon_hp = min(mon_hp + heal, monster["stats"]["hp"])
                    round_log.append(f"{monster['name']} 使用 {skill['description']} 回復了 {mon_hp - old_hp} 點生命值（目前 HP：{mon_hp}/{monster['stats']['hp']}）")
                elif skill_type == "buff":
                    buff = {
                        "name": skill.get("buffName", skill["description"]),
                        "description": skill["description"],
                        "multiplier": skill["multiplier"],
                        "effectType": skill.get("effectType", "atk"),
                        "round": skill.get("round", 3)
                    }
                    add_or_refresh_buff(mon_buffs, buff)
                    round_log.append(f"{monster['name']} 施放了 {buff['name']} ，{buff['description']}")
                
                elif skill_type == "debuff":
                    if calculate_hit(monster["stats"]["accuracy"] * mon_stats_mod["accuracy"],
                                     user["base_stats"]["evade"] * user_stats_mod["evade"],
                                     monster["stats"]["luck"]):
                        debuff = {
                            "name": skill.get("buffName", skill["description"]),
                            "description": skill["description"],
                            "multiplier": skill["multiplier"],
                            "effectType": skill.get("effectType", "atk"),
                            "round": skill.get("round", 3)
                        }
                        add_or_refresh_debuff(user_buffs, debuff)
                        round_log.append(f"{monster['name']} 對你施放了 {debuff['name']} ，{debuff['description']}")
                    else:
                        round_log.append(f"{monster['name']} 對你施放 {skill['buffName']} 但未命中")
                elif skill_type == "atk":
                    if calculate_hit(monster["stats"]["accuracy"] * mon_stats_mod["accuracy"],
                                     user["base_stats"]["evade"] * user_stats_mod["evade"],
                                     monster["stats"]["luck"]):
                        ele_mod = get_element_multiplier(skill.get("element", []), ["none"])
                        atk = monster["stats"]["attack"] * mon_stats_mod["attack"]
                        shield = user["base_stats"]["shield"] * user_stats_mod["shield"]
                        dmg = calculate_damage(atk, skill["multiplier"], monster["stats"].get("phys_bonus", 0), shield)
                        dmg = round(dmg * mon_level_mod * ele_mod * mon_stats_mod["all_damage"])
                        user_hp = max(user_hp - dmg, 0)
                        round_log.append(f"{monster['name']} 使用 {skill['description']} 對你造成 {dmg} 傷害（目前 HP：{user_hp}/{user['base_stats']['hp']}）")
                    else:
                        round_log.append(f"{monster['name']} 攻擊未命中")

                round_log.extend(buff_log)

        # if user_hp <= 0:
            # round_log.append("═══════════════ ☠️ 你已戰敗 ☠️ ═══════════════")
        # elif mon_hp <= 0:
            # round_log.append("═══════════════ 🌟 戰鬥結束 🌟 ═══════════════")

        log.append({"round": current_round, "actions": round_log})

        if user_hp <= 0 or mon_hp <= 0:
            break

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
