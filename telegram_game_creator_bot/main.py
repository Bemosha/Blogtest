import asyncio
import hashlib
import json
import logging
import os
import re
import time
import random
import uuid
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Optional

import requests
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters


load_dotenv()

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("telegram_game_creator_bot")


def env_required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Environment variable is required: {name}")
    return value


BOT_TOKEN = env_required("TELEGRAM_BOT_TOKEN")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "").strip()
GITHUB_OWNER = os.getenv("GITHUB_OWNER", "").strip()
GITHUB_REPO = os.getenv("GITHUB_REPO", "").strip()
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main").strip() or "main"
GAMES_BASE_PATH = os.getenv("GAMES_BASE_PATH", "generated_games").strip().strip("/")
GAME_LINK_BASE = os.getenv("GAME_LINK_BASE", "").strip()
BASE_DIR = Path(__file__).resolve().parent

AI_API_KEY = os.getenv("AI_API_KEY", "").strip()
AI_BASE_URL = os.getenv("AI_BASE_URL", "https://api.openai.com/v1").strip().rstrip("/")
AI_MODEL = os.getenv("AI_MODEL", "gpt-4.1-mini").strip()
AI_TIMEOUT_SECONDS = int(os.getenv("AI_TIMEOUT_SECONDS", "120"))


@dataclass
class GeneratedGame:
    game_id: str
    title: str
    description: str
    html: str
    github_path: str
    ai_used: bool


def slugify(text: str, fallback: str = "game") -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-zа-я0-9\s_-]", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    if not text:
        return fallback
    return text[:40]


def derive_title(description: str) -> str:
    cleaned = " ".join(description.split())
    if not cleaned:
        return "My Telegram Game"
    return cleaned[:48]


def pick_theme(description: str) -> Dict[str, str]:
    d = description.lower()
    if any(k in d for k in ["косм", "space", "галакт", "ship", "шутер", "shooter"]):
        return {
            "bg": "#070b34",
            "player": "#4ef3ff",
            "enemy": "#ff4e8a",
            "accent": "#89ff65",
            "title": "Space Dodge",
        }
    if any(k in d for k in ["гонк", "машин", "race", "car", "drift"]):
        return {
            "bg": "#1b1b1b",
            "player": "#58ff9b",
            "enemy": "#ff8a3d",
            "accent": "#fff14e",
            "title": "Road Escape",
        }
    return {
        "bg": "#111827",
        "player": "#60a5fa",
        "enemy": "#fb7185",
        "accent": "#a3e635",
        "title": "Neon Survival",
    }


def extract_html_from_ai_response(text: str) -> str:
    cleaned = text.strip()
    if "```" in cleaned:
        parts = cleaned.split("```")
        for part in parts:
            chunk = part.strip()
            if chunk.lower().startswith("html"):
                chunk = chunk[4:].strip()
            if "<html" in chunk.lower() and "</html>" in chunk.lower():
                return chunk
    return cleaned


def generate_game_with_ai(description: str) -> Optional[str]:
    if not AI_API_KEY:
        return None

    system_prompt = (
        "You are a senior web game developer. Generate ONE self-contained HTML file for a high-quality browser 3D game. "
        "Use Three.js from CDN. The game must be playable on desktop and mobile. "
        "No placeholders, no explanations, no markdown. Return ONLY full HTML. "
        "Include polished graphics, lights, particles/effects, HUD, restart, collisions, scoring, and smooth controls."
    )
    user_prompt = (
        f"Create a unique 3D game using this idea: {description}. "
        "Requirements: one HTML file, realistic/polished look, stable performance, responsive canvas, mouse/touch/keyboard controls, "
        "game loop and clear objective."
    )

    payload = {
        "model": AI_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.8,
    }

    try:
        resp = requests.post(
            f"{AI_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {AI_API_KEY}",
                "Content-Type": "application/json",
            },
            data=json.dumps(payload),
            timeout=AI_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        html = extract_html_from_ai_response(content)
        if "<html" not in html.lower():
            return None
        return html
    except Exception as e:
        logger.warning("AI generation failed, fallback to built-in 3D template: %s", e)
        return None


def description_variant(description: str) -> int:
    h = hashlib.sha256(description.encode("utf-8")).hexdigest()
    return int(h[:8], 16) % 3


def choose_fallback_variant(description: str, game_id: str) -> int:
    d = description.lower()
    salt = int(hashlib.sha256(game_id.encode("utf-8")).hexdigest()[:8], 16)
    rng = random.SystemRandom()
    if any(k in d for k in ["косм", "space", "галакт", "ship", "asteroid", "астероид", "шутер", "shooter"]):
        pool = [0, 2]  # mostly space, sometimes road
        return rng.choice(pool)
    if any(k in d for k in ["arena", "арен", "survival", "выж", "зомби", "monster", "монстр"]):
        pool = [1, 2]  # mostly arena, sometimes road
        return rng.choice(pool)
    if any(k in d for k in ["гонк", "race", "runner", "раннер", "дорог", "car", "машин"]):
        pool = [2, 0]  # mostly road, sometimes space
        return rng.choice(pool)
    pool = [0, 1, 2]
    random.Random(salt).shuffle(pool)
    return pool[0]


def fallback_space_shooter_html(title: str, desc: str, theme: Dict[str, str]) -> str:
    html = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>__TITLE__ - Space Shooter 3D</title>
<style>body{margin:0;overflow:hidden;background:#020617;color:#fff;font-family:Arial}#hud{position:fixed;top:10px;left:10px;background:#0008;padding:8px 10px;border-radius:10px}</style>
</head><body>
<div id="hud">🚀 __TITLE__<br/>Счёт: <span id="s">0</span><br/>Описание: __DESC__</div>
<script src="https://unpkg.com/three@0.160.1/build/three.min.js"></script>
<script>
const scene=new THREE.Scene();scene.fog=new THREE.Fog(0x020617,20,140);
const cam=new THREE.PerspectiveCamera(70,innerWidth/innerHeight,0.1,300);cam.position.set(0,4,12);
const r=new THREE.WebGLRenderer({antialias:true});r.setSize(innerWidth,innerHeight);document.body.appendChild(r.domElement);
scene.add(new THREE.HemisphereLight(0x99ccff,0x101020,1));const dl=new THREE.DirectionalLight(0xffffff,1);dl.position.set(6,10,8);scene.add(dl);
const stars=new THREE.Points(new THREE.BufferGeometry(),new THREE.PointsMaterial({color:0xffffff,size:0.12}));
const arr=new Float32Array(2400*3);for(let i=0;i<2400;i++){arr[i*3]=(Math.random()-.5)*200;arr[i*3+1]=(Math.random()-.5)*120;arr[i*3+2]=-Math.random()*260;}stars.geometry.setAttribute('position',new THREE.BufferAttribute(arr,3));scene.add(stars);
const ship=new THREE.Mesh(new THREE.ConeGeometry(0.8,2.2,10),new THREE.MeshStandardMaterial({color:'__PLAYER__',metalness:.6,roughness:.2}));ship.rotation.x=Math.PI/2;ship.position.z=6;scene.add(ship);
const bullets=[],rocks=[];let score=0,alive=true;const keys=new Set();
function spawn(){const m=new THREE.Mesh(new THREE.IcosahedronGeometry(.9+Math.random()*1.1,0),new THREE.MeshStandardMaterial({color:'__ENEMY__',roughness:.8}));m.position.set((Math.random()-.5)*16,(Math.random()-.5)*8,-80);scene.add(m);rocks.push(m);}
setInterval(()=>alive&&spawn(),420);
addEventListener('keydown',e=>keys.add(e.key));addEventListener('keyup',e=>keys.delete(e.key));
addEventListener('resize',()=>{cam.aspect=innerWidth/innerHeight;cam.updateProjectionMatrix();r.setSize(innerWidth,innerHeight)});
let t0=performance.now();function loop(t){const dt=Math.min(.033,(t-t0)/1000);t0=t;if(alive){
if(keys.has('ArrowLeft')||keys.has('a'))ship.position.x-=8*dt;if(keys.has('ArrowRight')||keys.has('d'))ship.position.x+=8*dt;
if(keys.has('ArrowUp')||keys.has('w'))ship.position.y+=6*dt;if(keys.has('ArrowDown')||keys.has('s'))ship.position.y-=6*dt;
ship.position.x=Math.max(-8,Math.min(8,ship.position.x));ship.position.y=Math.max(-4.5,Math.min(4.5,ship.position.y));
if(Math.random()<0.38){const b=new THREE.Mesh(new THREE.SphereGeometry(.14,8,8),new THREE.MeshBasicMaterial({color:0xa7f3d0}));b.position.copy(ship.position);b.position.z-=1;scene.add(b);bullets.push(b);}
for(let i=bullets.length-1;i>=0;i--){const b=bullets[i];b.position.z-=55*dt;if(b.position.z<-120){scene.remove(b);bullets.splice(i,1);continue;}for(let j=rocks.length-1;j>=0;j--){const o=rocks[j];if(b.position.distanceTo(o.position)<1.25){scene.remove(o);rocks.splice(j,1);scene.remove(b);bullets.splice(i,1);score+=10;break;}}}
for(let i=rocks.length-1;i>=0;i--){const o=rocks[i];o.position.z+=20*dt+score*0.003;o.rotation.x+=dt;o.rotation.y+=dt*1.4;if(o.position.distanceTo(ship.position)<1.3){alive=false;alert('Игра окончена. Счёт: '+score);}if(o.position.z>20){scene.remove(o);rocks.splice(i,1);}}
stars.position.z+=dt*12;if(stars.position.z>120)stars.position.z=0;document.getElementById('s').textContent=score;}
cam.position.x+=(ship.position.x-cam.position.x)*.08;cam.position.y+=(ship.position.y+3-cam.position.y)*.08;cam.lookAt(ship.position.x,ship.position.y,0);
r.render(scene,cam);requestAnimationFrame(loop);}requestAnimationFrame(loop);
</script></body></html>"""
    return (
        html.replace("__TITLE__", title)
        .replace("__DESC__", desc)
        .replace("__PLAYER__", theme["player"])
        .replace("__ENEMY__", theme["enemy"])
    )


def fallback_arena_html(title: str, desc: str, theme: Dict[str, str]) -> str:
    html = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>__TITLE__ - Arena 3D</title><style>body{margin:0;overflow:hidden;background:#0b1020;color:#fff}#hud{position:fixed;left:10px;top:10px;background:#0008;padding:8px;border-radius:10px;font-family:Arial}</style>
</head><body><div id="hud">⚔️ __TITLE__<br/>Время: <span id="t">0</span>с<br/>__DESC__</div>
<script src="https://unpkg.com/three@0.160.1/build/three.min.js"></script><script>
const scene=new THREE.Scene();scene.background=new THREE.Color(0x0b1020);const cam=new THREE.PerspectiveCamera(68,innerWidth/innerHeight,.1,200);cam.position.set(0,16,14);
const r=new THREE.WebGLRenderer({antialias:true});r.setSize(innerWidth,innerHeight);document.body.appendChild(r.domElement);
scene.add(new THREE.HemisphereLight(0x88aaff,0x102010,1.1));const l=new THREE.DirectionalLight(0xffffff,.9);l.position.set(8,16,5);scene.add(l);
const floor=new THREE.Mesh(new THREE.CircleGeometry(22,48),new THREE.MeshStandardMaterial({color:0x1f2937}));floor.rotation.x=-Math.PI/2;scene.add(floor);
const p=new THREE.Mesh(new THREE.CapsuleGeometry(.6,1.2,4,8),new THREE.MeshStandardMaterial({color:'__PLAYER__'}));p.position.y=1;scene.add(p);
const enemies=[];function addEnemy(){const e=new THREE.Mesh(new THREE.BoxGeometry(1.1,1.1,1.1),new THREE.MeshStandardMaterial({color:'__ENEMY__'}));const a=Math.random()*Math.PI*2;const d=14+Math.random()*6;e.position.set(Math.cos(a)*d,.55,Math.sin(a)*d);scene.add(e);enemies.push(e);}
setInterval(addEnemy,650);
const keys=new Set();addEventListener('keydown',e=>keys.add(e.key.toLowerCase()));addEventListener('keyup',e=>keys.delete(e.key.toLowerCase()));
let alive=true,start=performance.now(),last=start;function loop(n){const dt=Math.min(.033,(n-last)/1000);last=n;if(alive){
let vx=0,vz=0;if(keys.has('a'))vx-=1;if(keys.has('d'))vx+=1;if(keys.has('w'))vz-=1;if(keys.has('s'))vz+=1;const len=Math.hypot(vx,vz)||1;p.position.x+=vx/len*7*dt;p.position.z+=vz/len*7*dt;
const d=Math.hypot(p.position.x,p.position.z);if(d>20){p.position.x*=20/d;p.position.z*=20/d;}
for(let i=enemies.length-1;i>=0;i--){const e=enemies[i];const dx=p.position.x-e.position.x,dz=p.position.z-e.position.z;const dl=Math.hypot(dx,dz)||1;e.position.x+=dx/dl*(2.4+n*0.00003)*dt;e.position.z+=dz/dl*(2.4+n*0.00003)*dt;e.rotation.y+=dt*2.4;if(dl<1.05){alive=false;alert('Ты продержался '+Math.floor((n-start)/1000)+' сек');}}
document.getElementById('t').textContent=Math.floor((n-start)/1000);cam.position.x+=(p.position.x-cam.position.x)*.08;cam.position.z+=(p.position.z+14-cam.position.z)*.08;cam.lookAt(p.position.x,0,p.position.z);
}r.render(scene,cam);requestAnimationFrame(loop);}requestAnimationFrame(loop);
addEventListener('resize',()=>{cam.aspect=innerWidth/innerHeight;cam.updateProjectionMatrix();r.setSize(innerWidth,innerHeight)});
</script></body></html>"""
    return (
        html.replace("__TITLE__", title)
        .replace("__DESC__", desc)
        .replace("__PLAYER__", theme["player"])
        .replace("__ENEMY__", theme["enemy"])
    )


def generate_game(description: str) -> GeneratedGame:
    desc = " ".join(description.split())
    title = derive_title(desc)
    theme = pick_theme(desc)
    game_id = f"{int(time.time())}-{uuid.uuid4().hex[:6]}"
    safe_slug = slugify(title)
    github_path = f"{GAMES_BASE_PATH}/{game_id}-{safe_slug}/index.html"

    html = generate_game_with_ai(desc)
    ai_used = bool(html)
    if not html:
        variant = choose_fallback_variant(desc, game_id)
        if variant == 0:
            html = fallback_space_shooter_html(title, desc, theme)
        elif variant == 1:
            html = fallback_arena_html(title, desc, theme)
        else:
            html = f"""<!doctype html>
<html lang=\"ru\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>{theme['title']} 3D</title>
  <style>
    :root {{ color-scheme: dark; }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Inter, Segoe UI, Arial, sans-serif;
      overflow: hidden;
      background: radial-gradient(circle at 50% 20%, #1f2937 0%, {theme['bg']} 65%);
      color: #fff;
    }}
    #game {{ position: fixed; inset: 0; width: 100vw; height: 100vh; display: block; }}
    .hud {{
      position: fixed;
      top: 12px;
      left: 12px;
      right: 12px;
      display: flex;
      justify-content: space-between;
      gap: 8px;
      z-index: 10;
      pointer-events: none;
    }}
    .panel {{
      background: rgba(12, 16, 28, 0.72);
      border: 1px solid rgba(255,255,255,0.15);
      backdrop-filter: blur(6px);
      border-radius: 12px;
      padding: 8px 12px;
      font-size: 14px;
      line-height: 1.35;
    }}
    #gameOver {{
      position: fixed;
      inset: 0;
      display: none;
      place-items: center;
      background: rgba(0,0,0,0.45);
      z-index: 20;
    }}
    #gameOverBox {{
      background: #0f172a;
      border: 1px solid rgba(255,255,255,0.18);
      border-radius: 16px;
      padding: 20px;
      text-align: center;
      width: min(92vw, 420px);
    }}
    button {{
      border: 0;
      border-radius: 10px;
      padding: 10px 14px;
      background: {theme['accent']};
      color: #0a0a0a;
      font-weight: 700;
      cursor: pointer;
    }}
  </style>
</head>
<body>
  <canvas id=\"game\"></canvas>

  <div class=\"hud\">
    <div class=\"panel\"><b>{title}</b><br/>Описание: {desc}</div>
    <div class=\"panel\" id=\"score\">Счёт: 0<br/>Скорость: 1.0x</div>
  </div>

  <div id=\"gameOver\">
    <div id=\"gameOverBox\">
      <h2 style=\"margin-top:0\">Игра окончена</h2>
      <p id=\"finalScore\">Счёт: 0</p>
      <button id=\"restartBtn\">Играть снова</button>
    </div>
  </div>

  <script src=\"https://unpkg.com/three@0.160.1/build/three.min.js\"></script>
  <script>
    const canvas = document.getElementById('game');
    const scoreEl = document.getElementById('score');
    const gameOver = document.getElementById('gameOver');
    const finalScore = document.getElementById('finalScore');
    const restartBtn = document.getElementById('restartBtn');

    const scene = new THREE.Scene();
    scene.background = new THREE.Color('{theme['bg']}');
    scene.fog = new THREE.Fog('{theme['bg']}', 18, 85);

    const camera = new THREE.PerspectiveCamera(65, window.innerWidth / window.innerHeight, 0.1, 200);
    camera.position.set(0, 6, 12);
    camera.lookAt(0, 1.5, 0);

    const renderer = new THREE.WebGLRenderer({{ canvas, antialias: true }});
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(window.innerWidth, window.innerHeight);
    renderer.shadowMap.enabled = true;

    const hemi = new THREE.HemisphereLight(0x99ccff, 0x1a1a1a, 0.9);
    scene.add(hemi);
    const keyLight = new THREE.DirectionalLight(0xffffff, 1.1);
    keyLight.position.set(8, 12, 6);
    keyLight.castShadow = true;
    scene.add(keyLight);

    const roadGeo = new THREE.PlaneGeometry(18, 220);
    const roadMat = new THREE.MeshStandardMaterial({{ color: 0x101421, roughness: 0.92, metalness: 0.05 }});
    const road = new THREE.Mesh(roadGeo, roadMat);
    road.rotation.x = -Math.PI / 2;
    road.position.z = -75;
    road.receiveShadow = true;
    scene.add(road);

    const lineMat = new THREE.MeshStandardMaterial({{ color: 0x2a334f }});
    for (let i = 0; i < 36; i++) {{
      const lane = new THREE.Mesh(new THREE.BoxGeometry(0.25, 0.02, 2.8), lineMat);
      lane.position.set(0, 0.02, -i * 6);
      scene.add(lane);
    }}

    const starGeo = new THREE.BufferGeometry();
    const starCount = 1800;
    const positions = new Float32Array(starCount * 3);
    for (let i = 0; i < starCount; i++) {{
      positions[i * 3 + 0] = (Math.random() - 0.5) * 180;
      positions[i * 3 + 1] = Math.random() * 80 + 5;
      positions[i * 3 + 2] = -Math.random() * 260;
    }}
    starGeo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    const stars = new THREE.Points(starGeo, new THREE.PointsMaterial({{ color: 0xffffff, size: 0.12 }}));
    scene.add(stars);

    const ship = new THREE.Group();
    const body = new THREE.Mesh(
      new THREE.BoxGeometry(1.4, 0.9, 2.3),
      new THREE.MeshStandardMaterial({{ color: '{theme['player']}', roughness: 0.35, metalness: 0.45 }})
    );
    body.castShadow = true;
    ship.add(body);

    const cockpit = new THREE.Mesh(
      new THREE.BoxGeometry(0.8, 0.45, 0.8),
      new THREE.MeshStandardMaterial({{ color: 0xcceeff, roughness: 0.12, metalness: 0.75 }})
    );
    cockpit.position.set(0, 0.5, 0.25);
    cockpit.castShadow = true;
    ship.add(cockpit);
    ship.position.set(0, 0.75, 3.5);
    scene.add(ship);

    const lanes = [-4.8, -2.4, 0, 2.4, 4.8];
    let targetLane = 2;

    const obstacles = [];
    function makeObstacle() {{
      const h = 1 + Math.random() * 1.8;
      const mesh = new THREE.Mesh(
        new THREE.BoxGeometry(1.6, h, 1.6),
        new THREE.MeshStandardMaterial({{ color: '{theme['enemy']}', roughness: 0.5, metalness: 0.2 }})
      );
      mesh.castShadow = true;
      mesh.position.set(lanes[Math.floor(Math.random() * lanes.length)], h / 2, -80 - Math.random() * 40);
      scene.add(mesh);
      obstacles.push(mesh);
    }}

    let spawnTimer = 0;
    let speed = 24;
    let score = 0;
    let alive = true;
    const keys = new Set();
    let touchX = null;

    function restart() {{
      alive = true;
      speed = 24;
      score = 0;
      targetLane = 2;
      ship.position.set(0, 0.75, 3.5);
      for (const o of obstacles) scene.remove(o);
      obstacles.length = 0;
      spawnTimer = 0;
      gameOver.style.display = 'none';
    }}

    function hit(a, b) {{
      const dx = Math.abs(a.x - b.x);
      const dz = Math.abs(a.z - b.z);
      return dx < 1.2 && dz < 1.4;
    }}

    function update(dt) {{
      if (!alive) return;

      if (keys.has('ArrowLeft') || keys.has('a') || keys.has('A')) targetLane = Math.max(0, targetLane - 1);
      if (keys.has('ArrowRight') || keys.has('d') || keys.has('D')) targetLane = Math.min(lanes.length - 1, targetLane + 1);
      keys.clear();

      ship.position.x += (lanes[targetLane] - ship.position.x) * Math.min(1, dt * 12);
      ship.rotation.z = (lanes[targetLane] - ship.position.x) * -0.08;

      spawnTimer -= dt;
      if (spawnTimer <= 0) {{
        makeObstacle();
        spawnTimer = Math.max(0.22, 0.72 - score * 0.004);
      }}

      speed += dt * 0.35;
      for (let i = obstacles.length - 1; i >= 0; i--) {{
        const o = obstacles[i];
        o.position.z += speed * dt;
        o.rotation.x += dt * 0.9;
        o.rotation.y += dt * 1.2;

        if (hit(ship.position, o.position)) {{
          alive = false;
          finalScore.textContent = `Счёт: ${{Math.floor(score)}}`;
          gameOver.style.display = 'grid';
        }}

        if (o.position.z > 12) {{
          scene.remove(o);
          obstacles.splice(i, 1);
          score += 10;
        }}
      }}

      stars.position.z += dt * (8 + speed * 0.1);
      if (stars.position.z > 60) stars.position.z = 0;

      score += dt * 6;
      scoreEl.innerHTML = `Счёт: <b>${{Math.floor(score)}}</b><br/>Скорость: <b>${{(speed / 24).toFixed(2)}}x</b>`;
    }}

    let last = performance.now();
    function frame(now) {{
      const dt = Math.min(0.033, (now - last) / 1000);
      last = now;
      update(dt);

      camera.position.x += (ship.position.x * 0.35 - camera.position.x) * Math.min(1, dt * 4.2);
      camera.lookAt(ship.position.x * 0.2, 1.5, 0);
      renderer.render(scene, camera);
      requestAnimationFrame(frame);
    }}

    window.addEventListener('keydown', (e) => keys.add(e.key));
    window.addEventListener('resize', () => {{
      camera.aspect = window.innerWidth / window.innerHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(window.innerWidth, window.innerHeight);
    }});

    window.addEventListener('touchstart', (e) => {{ touchX = e.touches[0].clientX; }}, {{ passive: true }});
    window.addEventListener('touchmove', (e) => {{
      if (touchX == null) return;
      const x = e.touches[0].clientX;
      if (x - touchX > 18) targetLane = Math.min(lanes.length - 1, targetLane + 1);
      if (x - touchX < -18) targetLane = Math.max(0, targetLane - 1);
      touchX = x;
    }}, {{ passive: true }});
    window.addEventListener('touchend', () => {{ touchX = null; }}, {{ passive: true }});

    restartBtn.addEventListener('click', restart);
    requestAnimationFrame(frame);
  </script>
</body>
</html>
"""

    return GeneratedGame(
        game_id=game_id,
        title=title,
        description=desc,
        html=html,
        github_path=github_path,
        ai_used=ai_used,
    )


def github_headers() -> Dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def publish_to_github(game: GeneratedGame) -> Dict[str, str]:
    api_url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{game.github_path}"

    payload = {
        "message": f"Add generated game: {game.title}",
        "content": game.html.encode("utf-8").decode("utf-8"),
        "branch": GITHUB_BRANCH,
    }
    import base64

    payload["content"] = base64.b64encode(game.html.encode("utf-8")).decode("utf-8")

    resp = requests.put(api_url, headers=github_headers(), data=json.dumps(payload), timeout=25)
    if resp.status_code >= 300:
        raise RuntimeError(f"GitHub upload failed: {resp.status_code} {resp.text}")

    repo_file_url = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/blob/{GITHUB_BRANCH}/{game.github_path}"
    raw_file_url = f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/{GITHUB_BRANCH}/{game.github_path}"

    if GAME_LINK_BASE:
        game_url = f"{GAME_LINK_BASE.rstrip('/')}/{game.github_path}"
    else:
        game_url = f"https://raw.githack.com/{GITHUB_OWNER}/{GITHUB_REPO}/{GITHUB_BRANCH}/{game.github_path}"

    return {
        "repo_file_url": repo_file_url,
        "raw_file_url": raw_file_url,
        "game_url": game_url,
        "local_file_path": "",
    }


def save_game_locally(game: GeneratedGame) -> Dict[str, str]:
    full_path = BASE_DIR / game.github_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text(game.html, encoding="utf-8")
    return {
        "repo_file_url": "",
        "raw_file_url": "",
        "game_url": "",
        "local_file_path": str(full_path),
    }


def can_publish_to_github() -> bool:
    return bool(GITHUB_TOKEN and GITHUB_OWNER and GITHUB_REPO)


def publish_game(game: GeneratedGame) -> Dict[str, str]:
    if can_publish_to_github():
        return publish_to_github(game)
    return save_game_locally(game)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "Привет! Я бот, который создаёт HTML-игры.\n\n"
        "Отправь мне описание игры обычным сообщением (например: 'Космическая игра, где корабль уворачивается от астероидов').\n"
        "Я сгенерирую игру и отправлю результат. Если подключен AI API — качество и разнообразие будет намного выше. "
        "Если подключен GitHub — дам публичную ссылку."
    )
    await update.message.reply_text(text)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "/start — начать\n"
        "/help — помощь\n\n"
        "Просто отправь текст с описанием игры, и бот создаст её."
    )


async def on_description(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if not message or not message.text:
        return

    description = message.text.strip()
    if len(description) < 8:
        await message.reply_text("Опиши игру чуть подробнее (минимум 8 символов).")
        return

    status = await message.reply_text("Создаю игру... Это может занять до 1-2 минут при AI-генерации.")

    try:
        game = generate_game(description)
        urls = publish_game(game)
        quality_note = "🤖 AI-режим: включён" if game.ai_used else "⚠️ AI-режим: выключен (использован локальный шаблон)"

        if urls["game_url"]:
            await status.edit_text(
                "✅ Игра создана!\n\n"
                f"{quality_note}\n"
                f"🎮 Играть: {urls['game_url']}\n"
                f"💻 Код в GitHub: {urls['repo_file_url']}\n"
                f"📄 Raw HTML: {urls['raw_file_url']}"
            )
            return

        with open(urls["local_file_path"], "rb") as f:
            await message.reply_document(document=f, filename="index.html", caption="✅ Игра создана. Отправляю HTML-файл игры.")
        await status.edit_text(
            f"{quality_note}\n"
            "✅ Игра создана без GitHub.\n"
            f"Файл сохранён локально: {urls['local_file_path']}\n"
            "Если нужен публичный URL — позже можно подключить GitHub token."
        )
    except Exception as e:
        logger.exception("Game generation failed: %s", e)
        await status.edit_text(
            "❌ Не получилось создать игру. Проверь настройки и попробуй ещё раз."
        )


def main() -> None:
    # Python 3.14+: ensure an active event loop in main thread for run_polling
    asyncio.set_event_loop(asyncio.new_event_loop())

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_description))

    logger.info("Game creator bot started")
    app.run_polling()


if __name__ == "__main__":
    main()
