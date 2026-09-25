#!/usr/bin/env python3
"""ARC-AGI-3 public-game prototype with local scoring and Bedrock agents."""

import argparse
from importlib.metadata import version
import json
import logging
import os
from pathlib import Path
import re
import sys

from arc_common import (BedrockSession, RunLimit, add_common_arguments, digest,
                        finish, new_run, positive_int, write_json)

GAMES = ("ls20-9607627b", "ft09-0d8bbf25", "vc33-5430563c")
PROMPT = (
    "Explore this unfamiliar grid game and discover how to complete its levels. "
    "You see observations and available action IDs, with no game instructions. "
    "Return one action as JSON only: {\"action\":1} or {\"action\":6,\"x\":0,\"y\":0}. "
    "Action 6 uses zero-indexed x,y coordinates in the grid. Action 0 resets the game. "
    "Choose an available action, learn from the result, and try to finish efficiently."
)


def observation(frame):
    """Only public observations reach the agent, never engine state or baselines."""
    if frame is None:
        raise RuntimeError("environment returned no observation")
    frames = [f.tolist() if hasattr(f, "tolist") else f for f in frame.frame]
    return {
        "frames": frames, "state": frame.state.value,
        "levels_completed": frame.levels_completed,
        "available_actions": list(frame.available_actions),
    }


def parse_action(text, obs):
    match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text.strip(), flags=re.DOTALL)
    if match:
        text = match.group(1)
    try:
        action = json.loads(text)
    except (ValueError, TypeError) as e:
        raise ValueError("action must be a JSON object") from e
    if not isinstance(action, dict) or set(action) - {"action", "x", "y"}:
        raise ValueError("unexpected action fields")
    number = action.get("action")
    if type(number) is not int or number not in [0, *obs["available_actions"]]:
        raise ValueError("action is not available")
    if number == 6:
        grid = obs["frames"][-1]
        if not all(type(action.get(k)) is int for k in ("x", "y")):
            raise ValueError("action 6 requires integer x and y")
        if not (0 <= action["y"] < len(grid) and 0 <= action["x"] < len(grid[0])):
            raise ValueError("coordinates are outside the frame")
    elif "x" in action or "y" in action:
        raise ValueError("coordinates are only valid for action 6")
    return action


def retain_output(history, output, *, plain_messages=False):
    for item in output:
        if item.get("type") == "message" and (
            plain_messages or not item.get("id") or not item.get("status")
            or any(p.get("type") == "output_text" and "annotations" not in p
                   for p in item.get("content", []))
        ):
            # Mantle's GPT-OSS path rejects replayed output_text messages even
            # when response metadata is present. Use the equivalent input-message
            # form. Reasoning/compaction items remain untouched.
            text = "".join(p.get("text", p.get("refusal", "")) for p in item.get("content", []))
            history.append({"role": "assistant", "content": text})
        else:
            history.append(item)
    # Server compaction items retain the earlier context; never truncate without one.
    indices = [i for i, item in enumerate(history) if item.get("type") == "compaction"]
    if indices:
        del history[:indices[-1]]


def play_game(session, env, max_actions, compaction_threshold, checkpoint):
    from arcengine import GameAction
    session.reset_context()
    current = observation(env.reset())
    row = {"game_id": env.environment_info.game_id, "status": "running",
           "initial_observation": current, "turns": [], "actions": 0}
    history = [{"role": "user", "content": PROMPT}]
    checkpoint(row)
    for _ in range(max_actions):
        if current["state"] == "WIN":
            row["status"] = "won"
            break
        history.append({"role": "user", "content": json.dumps(current, separators=(",", ":"))})
        result = session.call(history, compaction_threshold=compaction_threshold)
        row["turns"].append(result)
        checkpoint(row)
        if result["status"] != "completed":
            row["status"] = "incomplete_response"
            break
        try:
            action = parse_action(result["text"], current)
        except ValueError as e:
            result["action_error"] = str(e)
            row["status"] = "invalid_action"
            break
        retain_output(history, result["output"],
                      plain_messages=getattr(session, "plain_messages", False))
        payload = {k: action[k] for k in ("x", "y") if k in action}
        current = observation(env.step(GameAction.from_id(action["action"]), data=payload))
        result.update(action=action, observation=current)
        row["actions"] += 1
        checkpoint(row)
    if row["status"] == "running":
        row["status"] = "won" if current["state"] == "WIN" else "action_limit"
    row.update(levels_completed=current["levels_completed"], final_state=current["state"])
    checkpoint(row)
    return row


def make_arcade(args, mode):
    from arc_agi import Arcade, OperationMode
    if os.environ.get("OPERATION_MODE", "").lower() == "competition":
        raise ValueError("unset OPERATION_MODE=competition for this local public-game pilot")
    logger = logging.getLogger("arc-pilot")
    logger.setLevel(logging.WARNING)
    logger.propagate = False
    return Arcade(
        operation_mode=OperationMode(mode), environments_dir=str(args.environments_dir.resolve()),
        recordings_dir=str((args.output_dir / "recordings").resolve()), logger=logger)


def environment_inventory(root):
    files = {str(p.relative_to(root)): digest(p.read_bytes())
             for p in sorted(root.rglob("*"))
             if p.is_file() and p.suffix in (".py", ".json")}
    if not files:
        raise ValueError("no local environments; use --prepare first")
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_arguments(parser)
    parser.add_argument("--environments-dir", type=Path, required=True)
    parser.add_argument("--games", default=",".join(GAMES), help="comma-separated versioned game IDs")
    parser.add_argument("--max-actions", type=positive_int, default=40)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--compact-threshold", type=positive_int,
                        help="opt in after confirming Bedrock endpoint support; no silent fallback")
    parser.add_argument("--prepare", action="store_true",
                        help="download public environments using the SDK; no model calls")
    args = parser.parse_args()
    if args.prepare and args.execute:
        parser.error("--prepare and --execute are separate operations")
    games = args.games.split(",")
    if len(set(games)) != len(games) or any("-" not in game for game in games):
        parser.error("choose unique versioned game IDs")
    try:
        run = new_run(args, "arc-agi-3")
    except (ValueError, OSError) as e:
        parser.error(str(e))
    run.update(games=games, seed=args.seed, max_actions_per_game=args.max_actions,
               planned_max_calls=len(games) * args.max_actions,
               reasoning_strategy="visible assistant text only (Safeguard Chat Completions)"
               if "gpt-oss-safeguard-" in args.model else
               "replay all returned output items; request encrypted reasoning for frontier models",
               compaction_threshold=args.compact_threshold,
               prompt_sha256=digest(PROMPT.encode()))
    session, arcade, card_id = None, None, None
    code = 0
    try:
        run["sdk_versions"] = {p: version(p) for p in ("arc-agi", "arcengine")}
        if args.prepare:
            arcade = make_arcade(args, "normal")
            card_id = arcade.open_scorecard()
            for game in games:
                env = arcade.make(game, scorecard_id=card_id, seed=args.seed)
                if env is None or env.environment_info.game_id != game:
                    raise RuntimeError(f"could not prepare exact environment {game}")
            run["status"] = "prepared"
        run["environment_sha256"] = environment_inventory(args.environments_dir)
        if args.execute:
            arcade = make_arcade(args, "offline")
            available = {e.game_id for e in arcade.get_environments()}
            if not set(games) <= available:
                raise ValueError("requested game versions missing from local cache")
            session = BedrockSession(args)
            card_id = arcade.open_scorecard()
            run["status"] = "running"
            for game in games:
                env = arcade.make(game, scorecard_id=card_id, seed=args.seed, save_recording=True)
                if env is None:
                    raise RuntimeError(f"could not load {game}")
                def checkpoint(row):
                    if not run["results"] or run["results"][-1]["game_id"] != row["game_id"]:
                        run["results"].append(row)
                    write_json(args.output_dir / "manifest.json", run)
                play_game(session, env, args.max_actions, args.compact_threshold, checkpoint)
            run["status"] = "completed"
    except RunLimit as e:
        run.update(status="limited", error=str(e))
        code = 2
    except KeyboardInterrupt:
        run.update(status="interrupted")
        code = 130
    except Exception as e:
        run.update(status="failed", error_type=type(e).__name__, error=str(e))
        code = 1
    finally:
        if arcade is not None and card_id is not None:
            try:
                card = arcade.close_scorecard(card_id)
                if card is not None and args.execute:
                    run["sdk_scorecard"] = card.model_dump(mode="json", exclude={"api_key"})
                    # Partial SDK scorecards average played games only.
                    run["pilot_score"] = card.score if len(run["results"]) == len(games) \
                        and run["status"] == "completed" else None
            except Exception as e:
                run.update(status="failed", scorecard_error=str(e))
                code = 1
        finish(run, args, session)
    print(f"{run['status']}: {args.output_dir / 'manifest.json'}")
    return code


if __name__ == "__main__":
    sys.exit(main())
