from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import uuid
import json
import random
import logging
import asyncio
import logging

logging.basicConfig(level=logging.INFO)

app = FastAPI()
connected_users = {}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    logging.info("[CONNECT]WebSocket 接続を受け付けました")
    await websocket.accept()

    init_message = await websocket.receive_text()
    init_data = json.loads(init_message)

    print(f"[SERVER]最初のデータ受信:{init_data}")
    if init_data.get("type") == "init":
         user_id = init_data.get("user_id")
         name = init_data.get("name")
         print(f"[INIT] user_id:{user_id},name:{name}")

         if not user_id or user_id not in connected_users:
             connected_users[user_id] = {
             "socket": websocket,
             "name": None,
             "status": "waiting",
             "opponent": None
              }
        
         else:
             connected_users[user_id]["socket"] = websocket
             if connected_users[user_id]["opponent"]:
                 opponent_id = connected_users[user_id]["opponent"]
                 if opponent_id in connected_users:
                     opponent_socket = connected_users[opponent_id]["socket"]
                     await opponent_socket.send_text(json.dumps({
                         "type": "opponent_reconnected"
                     }))
    try:
        while True:
            message = await websocket.receive_text()
            data = json.loads(message)

            if data.get("type") == "register":
                print(f"[REGISTER]{user_id}({data.get('name')})が登録されました")
                connected_users[user_id]["status"] = "waiting"
                connected_users[user_id]["name"] = data.get("name")
                asyncio.create_task(try_match(user_id))
                
                

            elif data.get("type") == "move":
                opponent_id = connected_users[user_id]["opponent"]
                if opponent_id and opponent_id in connected_users:
                    opponent_socket = connected_users[opponent_id]["socket"]
                    await opponent_socket.send_text(json.dumps({
                        "type": "move",
                        "x": data["x"],
                        "y": data["y"]
                    }))

    except WebSocketDisconnect:
        print(f"{user_id} が切断されました")
        opponent_id = connected_users[user_id]["opponent"]
        if opponent_id and opponent_id in connected_users:
            try:
                await connected_users[opponent_id]["socket"].send_text(json.dumps({
                    "type": "opponent_disconnected"
                }))
                connected_users[opponent_id]["status"] = "waiting"
                connected_users[opponent_id]["opponent"] = None
            except:
                pass
        del connected_users[user_id]

async def try_match(current_id):
    waiting_users = [uid for uid, info in connected_users.items() if info["status"] == "waiting"]
    if len(waiting_users) >= 2:
        
        for other_id in waiting_users:
            if other_id !=current_id:
                user1_id = current_id
                user2_id = other_id
                break
        else:
            return

        if user1_id not in connected_users or user2_id not in connected_users:
            return  # 念のため再確認

        user1 = connected_users[user1_id]
        user2 = connected_users[user2_id]

        # 色と先手をランダムに決定
        colors = ["black", "white"]
        random.shuffle(colors)
        first_turn = "black"

        # 対戦情報を登録
        user1["status"] = "matched"
        user2["status"] = "matched"
        user1["opponent"] = user2_id
        user2["opponent"] = user1_id

        print(f"[MATCH SUCCESS]{user1_id}と{user2_id}がマッチしました")

        await asyncio.sleep(5.0)

        await user1["socket"].send_text(json.dumps({
            "type": "start_game",
            "your_color": colors[0],
            "opponent_name": user2["name"],
            "first_turn": first_turn
        }))

        await user2["socket"].send_text(json.dumps({
            "type": "start_game",
            "your_color": colors[1],
            "opponent_name": user1["name"],
            "first_turn": first_turn
        }))