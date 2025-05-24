from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import uuid
import json
import random
import logging
import asyncio
import redis.asyncio as redis
import os
from dotenv import load_dotenv


load_dotenv()  # .env を読み込む

redis_url = os.getenv("REDIS_URL")
rdb = redis.from_url(redis_url, decode_responses=True)

logging.basicConfig(level=logging.INFO)

app = FastAPI()


connected_sockets = {}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    logging.info("[CONNECT] WebSocket 接続開始")
            # 再接続時に盤面・ターンを復元送信
    
    await websocket.accept()

    try:
        init_message = await websocket.receive_text()
        init_data = json.loads(init_message)

        user_id = init_data.get("user_id")
        name = init_data.get("name")
        print(f"[INIT] user_id:{user_id}, name:{name}")

        board_data = await rdb.get(f"board:{user_id}")
        turn = await rdb.get(f"turn:{user_id}")

        if board_data and turn:
            await websocket.send_text(json.dumps({
                "type": "restore_board",
                "board": json.loads(board_data),
                "current_player": turn
            }))

        await rdb.hset(f"user:{user_id}", mapping={
            "name": name,
            "status": "waiting",
            "opponent": ""
        })
        connected_sockets[user_id] = websocket

        # 再接続時の通知
        opponent_id = await rdb.hget(f"user:{user_id}", "opponent")
        if opponent_id:
            opponent_socket = connected_sockets.get(opponent_id)
            if opponent_socket:
                await opponent_socket.send_text(json.dumps({
                    "type": "opponent_reconnected"
                }))

        while True:
            message = await websocket.receive_text()
            data = json.loads(message)

            if data.get("type") == "register":
                await rdb.hset(f"user:{user_id}", mapping={
                    "name": name,
                    "status": "waiting"
                })
                asyncio.create_task(try_match(user_id))

            elif data.get("type") == "move":
                 opponent_id = await rdb.hget(f"user:{user_id}", "opponent")

                 await rdb.set(f"board:{user_id}", json.dumps(data["board"]), ex=40)
                 await rdb.set(f"board:{opponent_id}", json.dumps(data["board"]), ex=40)
                 await rdb.set(f"turn:{user_id}", data["next_turn"], ex=40)
                 await rdb.set(f"turn:{opponent_id}", data["next_turn"], ex=40)

                 if opponent_id in connected_sockets:
                     
                     opponent_color = "black"if data["next_turn"]=="white"else"white"
                     await connected_sockets[opponent_id].send_text(json.dumps({
                          "type": "move",
                          "x": data["x"],
                          "y": data["y"],
                          "board":data["board"],
                          "next_turn":data["next_turn"],
                          "your_color": "black"if turn =="black"else"white"
                     }))
            elif data.get("type") == "pass":
                opponent_id = await rdb.hget(f"user:{user_id}", "opponent")

    # 現在の盤面とターン情報を保存
                await rdb.set(f"board:{user_id}", json.dumps(data["board"]), ex=40)
                await rdb.set(f"board:{opponent_id}", json.dumps(data["board"]), ex=40)
                await rdb.set(f"turn:{user_id}", data["next_turn"], ex=40)
                await rdb.set(f"turn:{opponent_id}", data["next_turn"], ex=40)

                if opponent_id in connected_sockets:
                     await connected_sockets[opponent_id].send_text(json.dumps({
                     "type": "pass"
                     }))
            elif data.get("type") == "end_game":
                opponent_id = await rdb.hget(f"user:{user_id}", "opponent")

    
                await rdb.delete(f"board:{user_id}")
                await rdb.delete(f"board:{opponent_id}")
                await rdb.delete(f"turn:{user_id}")
                await rdb.delete(f"turn:{opponent_id}")

    
                if opponent_id in connected_sockets:
                     await connected_sockets[opponent_id].send_text(json.dumps({
                    "type": "end_game"
                     }))


    except WebSocketDisconnect:
        logging.info(f"[DISCONNECT] {user_id} が切断されました")
        await handle_disconnect(user_id)

async def try_match(current_id):
    all_keys = await rdb.keys("user:*")
    waiting_users = []
    for key in all_keys:
        uid = key.split(":")[1]
        status = await rdb.hget(key, "status")
        if status == "waiting":
            waiting_users.append(uid)

    if len(waiting_users) < 2:
        return

    user1_id = current_id
    user2_id = next((uid for uid in waiting_users if uid != user1_id), None)
    if not user2_id:
        return

    user1_name = await rdb.hget(f"user:{user1_id}", "name")
    user2_name = await rdb.hget(f"user:{user2_id}", "name")

    # 色と先手をランダムに
    colors = ["black", "white"]
    random.shuffle(colors)
    first_turn = random.choice(["black", "white"])
    first_turn = colors[0]

    user1_color = colors[0]
    user2_color = colors[1]



    await rdb.hset(f"user:{user1_id}", mapping={"status": "matched", "opponent": user2_id})
    await rdb.hset(f"user:{user2_id}", mapping={"status": "matched", "opponent": user1_id})

    print(f"[MATCH] {user1_id} vs {user2_id}")

    await asyncio.sleep(2.0)

    await connected_sockets[user1_id].send_text(json.dumps({
        "type": "start_game",
        "your_color": user1_color,
        "opponent_name": user2_name,
        "first_turn": first_turn
    }))
    await connected_sockets[user2_id].send_text(json.dumps({
        "type": "start_game",
        "your_color": user2_color,
        "opponent_name": user1_name,
        "first_turn": first_turn
    }))
    save_board = [[0]*8 for _ in range(8)]
    mid = 4
    save_board[mid - 1][mid - 1] = -1
    save_board[mid][mid] = -1
    save_board[mid - 1][mid] = 1
    save_board[mid][mid - 1] = 1

    await rdb.set(f"board:{user1_id}", json.dumps(save_board), ex=40)
    await rdb.set(f"board:{user2_id}", json.dumps(save_board), ex=40)
    await rdb.set(f"turn:{user1_id}", first_turn, ex=40)
    await rdb.set(f"turn:{user2_id}", first_turn, ex=40)
    

async def handle_disconnect(user_id):
    opponent_id = await rdb.hget(f"user:{user_id}", "opponent")
    await rdb.delete(f"user:{user_id}")
    connected_sockets.pop(user_id, None)

    if opponent_id and opponent_id in connected_sockets:
        try:
            await connected_sockets[opponent_id].send_text(json.dumps({
                "type": "opponent_disconnected"
            }))
            await rdb.hset(f"user:{opponent_id}", mapping={
                "status": "waiting",
                "opponent": ""
            })
        except:
            pass
