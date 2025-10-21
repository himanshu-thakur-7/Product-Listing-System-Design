"""
product_catalog_api.py

Pygame product catalog UI that talks to a Flask backend.
GET /products -> lists products (reads replica)
POST /admin/products -> create (writes primary)
PATCH /admin/products/<id> -> update (writes primary)

Dependencies: pygame, requests
Run: API must be running (default http://localhost:5000), then:
    python product_catalog_api.py
"""

import os
import sys
import io
import threading
import time
import requests
import pygame
from pygame.locals import *

# -------------------
# Config
# -------------------
API_HOST = os.getenv("API_HOST", "localhost")
API_PORT = int(os.getenv("API_PORT", 5000))
API_BASE = f"http://{API_HOST}:{API_PORT}"
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "change-me")

IMAGE_CACHE_DIR = "image_cache"
SCREEN_SIZE = (1000, 700)
BG_COLOR = (245, 247, 250)
CARD_BG = (255, 255, 255)
CARD_SHADOW = (200, 200, 200)
ACCENT = (20, 90, 170)
TEXT = (30, 30, 30)
MUTED = (110, 110, 110)
FONT_NAME = None
CARD_WIDTH = 220
CARD_HEIGHT = 300
CARD_MARGIN = 24
COLUMNS = 4
SCROLL_SPEED = 20
REQUEST_TIMEOUT = 8

# ensure cache dir
os.makedirs(IMAGE_CACHE_DIR, exist_ok=True)

# -------------------
# Image utilities (download + cache)
# -------------------
def cache_path_for_url(url):
    safe = url.replace("://", "_").replace("/", "_").replace("?", "_").replace("&", "_")
    return os.path.join(IMAGE_CACHE_DIR, safe)

def download_image(url):
    if not url:
        return None
    path = cache_path_for_url(url)
    if os.path.exists(path):
        try:
            with open(path, "rb") as f:
                return f.read()
        except Exception:
            pass
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.content
            with open(path, "wb") as f:
                f.write(data)
            return data
    except Exception:
        return None

def load_image_surface_from_bytes(data, size):
    try:
        bio = io.BytesIO(data)
        img = pygame.image.load(bio).convert_alpha()
        img = pygame.transform.smoothscale(img, size)
        return img
    except Exception:
        return None

def placeholder_surface(size, text="No Image"):
    surf = pygame.Surface(size)
    surf.fill((235, 238, 243))
    pygame.draw.rect(surf, (220,220,220), surf.get_rect(), border_radius=8)
    font = pygame.font.SysFont(FONT_NAME, 18)
    t = font.render(text, True, (160,160,160))
    tx = (size[0]-t.get_width())//2
    ty = (size[1]-t.get_height())//2
    surf.blit(t, (tx, ty))
    return surf

# -------------------
# API helpers
# -------------------
def fetch_products_from_api():
    """
    Fetch products from GET /products
    Returns list of dicts or raises requests exception
    """
    url = f"{API_BASE}/products"
    r = requests.get(url, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    # Expecting {"products": [ {product_id, product_name, price, product_image_url}, ... ]}
    return data.get("products", [])

def create_product_api(name, price, image_url):
    url = f"{API_BASE}/admin/products"
    headers = {"X-Admin-Token": ADMIN_TOKEN, "Content-Type": "application/json"}
    payload = {"product_name": name, "price": price, "product_image_url": image_url}
    r = requests.post(url, json=payload, headers=headers, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json().get("product")

def update_product_api(product_id, fields):
    url = f"{API_BASE}/admin/products/{product_id}"
    headers = {"X-Admin-Token": ADMIN_TOKEN, "Content-Type": "application/json"}
    r = requests.patch(url, json=fields, headers=headers, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json().get("product")

# -------------------
# UI helpers
# -------------------
def draw_rounded_rect(surface, rect, color, radius=8, border=0, border_color=(0,0,0)):
    pygame.draw.rect(surface, color, rect, border_radius=radius)
    if border:
        pygame.draw.rect(surface, border_color, rect, border, border_radius=radius)

class Button:
    def __init__(self, rect, text, callback=None, bg=ACCENT, fg=(255,255,255), radius=8):
        self.rect = pygame.Rect(rect)
        self.text = text
        self.callback = callback
        self.bg = bg
        self.fg = fg
        self.radius = radius
        self.hover = False

    def draw(self, surf, font):
        color = tuple(min(255, int(c*1.05)) for c in self.bg) if self.hover else self.bg
        draw_rounded_rect(surf, self.rect, color, radius=self.radius)
        txt = font.render(self.text, True, self.fg)
        surf.blit(txt, (self.rect.x + (self.rect.width - txt.get_width())//2, self.rect.y + (self.rect.height - txt.get_height())//2))

    def handle_event(self, evt):
        if evt.type == MOUSEMOTION:
            self.hover = self.rect.collidepoint(evt.pos)
        elif evt.type == MOUSEBUTTONDOWN and evt.button == 1:
            if self.rect.collidepoint(evt.pos):
                if self.callback:
                    self.callback()

class InputBox:
    def __init__(self, rect, text="", placeholder="", numeric=False):
        self.rect = pygame.Rect(rect)
        self.text = text
        self.placeholder = placeholder
        self.active = False
        self.numeric = numeric
        self.cursor_timer = 0
        self.cursor_visible = True

    def draw(self, surf, font):
        color = (255,255,255) if self.active else (250,250,250)
        draw_rounded_rect(surf, self.rect, color, radius=6, border=1, border_color=(220,220,220))
        display = self.text if self.text else self.placeholder
        col = TEXT if self.text else MUTED
        txt = font.render(display, True, col)
        surf.blit(txt, (self.rect.x+8, self.rect.y+ (self.rect.height - txt.get_height())//2))
        if self.active:
            self.cursor_timer += 1/60
            if int(self.cursor_timer*2) % 2 == 0:
                self.cursor_visible = True
            else:
                self.cursor_visible = False
            if self.cursor_visible:
                cx = self.rect.x + 8 + txt.get_width() + 1
                cy = self.rect.y + 8
                ch = self.rect.height - 16
                pygame.draw.line(surf, TEXT, (cx, cy), (cx, cy+ch), 2)

    def handle_event(self, evt):
        if evt.type == MOUSEBUTTONDOWN:
            self.active = self.rect.collidepoint(evt.pos)
        elif evt.type == KEYDOWN and self.active:
            if evt.key == K_BACKSPACE:
                self.text = self.text[:-1]
            elif evt.key == K_RETURN:
                self.active = False
            else:
                ch = evt.unicode
                if self.numeric:
                    if ch.isdigit():
                        self.text += ch
                else:
                    self.text += ch

    def value(self):
        return self.text.strip()

# -------------------
# App
# -------------------
class CatalogApp:
    def __init__(self):
        pygame.init()
        pygame.display.set_caption("Product Catalog — API-backed")
        self.screen = pygame.display.set_mode(SCREEN_SIZE)
        self.font = pygame.font.SysFont(FONT_NAME, 18)
        self.title_font = pygame.font.SysFont(FONT_NAME, 26, bold=True)
        self.small_font = pygame.font.SysFont(FONT_NAME, 14)
        self.clock = pygame.time.Clock()

        self.products = []  # list of dicts: product_id, product_name, price, product_image_url
        self.scroll = 0
        self.loading_images = {}
        self.image_surfaces = {}  # product_id -> pygame Surface
        self.admin_open = False
        self.selected_edit_id = None
        self.message = ""  # brief status/error shown in header

        self.build_ui()
        # initial load
        self.reload_products_async()

    def build_ui(self):
        self.btn_admin = Button((SCREEN_SIZE[0]-140, 12, 120, 40), "Admin", callback=self.open_admin, bg=(30,120,200))
        self.btn_refresh = Button((SCREEN_SIZE[0]-280, 12, 120, 40), "Refresh", callback=self.reload_products_async, bg=(90, 90, 90))
        self.buttons = [self.btn_admin, self.btn_refresh]

    # -------------------
    # API <-> UI
    # -------------------
    def reload_products_async(self):
        self.message = "Loading products..."
        t = threading.Thread(target=self._reload_products_worker, daemon=True)
        t.start()

    def _reload_products_worker(self):
        try:
            data = fetch_products_from_api()
            # normalize into list of dicts
            # server returns list of dicts with keys product_id, product_name, price, product_image_url
            self.products = data
            # ensure image placeholders and spawn downloads
            for p in self.products:
                pid = p.get("product_id")
                url = p.get("product_image_url")
                if pid not in self.image_surfaces:
                    self.image_surfaces[pid] = placeholder_surface((CARD_WIDTH-24, 160))
                if url and pid not in self.loading_images:
                    th = threading.Thread(target=self._download_and_cache_image, args=(pid, url), daemon=True)
                    self.loading_images[pid] = th
                    th.start()
            self.message = f"Loaded {len(self.products)} products"
        except Exception as e:
            self.message = f"Failed to load: {str(e)}"

    def _download_and_cache_image(self, pid, url):
        data = download_image(url)
        if data:
            surf = load_image_surface_from_bytes(data, (CARD_WIDTH-24, 160))
            if surf:
                self.image_surfaces[pid] = surf
                return
        # fallback
        name = next((p.get("product_name") for p in self.products if p.get("product_id")==pid), "No image")
        self.image_surfaces[pid] = placeholder_surface((CARD_WIDTH-24, 160), text=name[:12])

    def open_admin(self):
        self.admin_open = True
        self.selected_edit_id = None
        w = 520; h = 360
        x = (SCREEN_SIZE[0]-w)//2; y = (SCREEN_SIZE[1]-h)//2
        self.admin_rect = pygame.Rect(x, y, w, h)
        self.input_name = InputBox((x+24, y+70, w-48, 44), placeholder="Product name")
        self.input_price = InputBox((x+24, y+140, w-200, 44), placeholder="Price (INR)", numeric=True)
        self.input_image = InputBox((x+24, y+210, w-48, 44), placeholder="Image URL (http...)")
        self.btn_save = Button((x + w - 160, y + h - 60, 120, 40), "Save", callback=self.save_admin, bg=(20,120,80))
        self.btn_cancel = Button((x + w - 300, y + h - 60, 120, 40), "Cancel", callback=self.close_admin, bg=(160, 40, 40))
        self.form_elements = [self.input_name, self.input_price, self.input_image, self.btn_save, self.btn_cancel]

    def open_edit(self, product):
        self.open_admin()
        self.selected_edit_id = product.get("product_id")
        self.input_name.text = product.get("product_name", "")
        self.input_price.text = str(product.get("price", ""))
        self.input_image.text = product.get("product_image_url") or ""

    def close_admin(self):
        self.admin_open = False
        self.selected_edit_id = None

    def save_admin(self):
        name = self.input_name.value()
        price = self.input_price.value()
        image = self.input_image.value()
        if not name:
            self.message = "Name required"
            return
        if not price or not price.isdigit():
            self.message = "Price must be integer"
            return
        price = int(price)
        # perform API create/update in background
        self.message = "Saving..."
        t = threading.Thread(target=self._save_admin_worker, args=(name, price, image, self.selected_edit_id), daemon=True)
        t.start()

    def _save_admin_worker(self, name, price, image, selected_id):
        try:
            if selected_id:
                updated = update_product_api(selected_id, {"product_name": name, "price": price, "product_image_url": image})
                self.message = f"Updated {updated.get('product_name')}"
            else:
                created = create_product_api(name, price, image)
                self.message = f"Created {created.get('product_name')}"
                # ensure placeholder and start download
                pid = created.get("product_id")
                self.image_surfaces[pid] = placeholder_surface((CARD_WIDTH-24, 160))
                if image:
                    th = threading.Thread(target=self._download_and_cache_image, args=(pid, image), daemon=True)
                    self.loading_images[pid] = th
                    th.start()
            # reload products after change
            time.sleep(0.4)
            self.reload_products_async()
        except requests.HTTPError as http_err:
            try:
                msg = http_err.response.json()
            except Exception:
                msg = str(http_err)
            self.message = f"API error: {msg}"
        except Exception as e:
            self.message = f"Save failed: {str(e)}"
        finally:
            self.admin_open = False

    # -------------------
    # Input handling & drawing
    # -------------------
    def run(self):
        running = True
        while running:
            dt = self.clock.tick(60)
            for evt in pygame.event.get():
                if evt.type == QUIT:
                    running = False
                elif evt.type == MOUSEBUTTONDOWN:
                    if evt.button == 4:
                        self.scroll = max(self.scroll - SCROLL_SPEED, 0)
                    elif evt.button == 5:
                        self.scroll += SCROLL_SPEED
                for b in self.buttons:
                    b.handle_event(evt)
                if self.admin_open:
                    for e in self.form_elements:
                        if isinstance(e, InputBox):
                            e.handle_event(evt)
                        else:
                            e.handle_event(evt)
                    if evt.type == MOUSEBUTTONDOWN and evt.button == 1:
                        # click outside admin doesn't auto-close to avoid lost edits
                        pass
                else:
                    if evt.type == MOUSEBUTTONDOWN and evt.button == 1:
                        self.handle_click_on_grid(evt.pos)
                if evt.type == MOUSEMOTION:
                    for b in self.buttons:
                        b.handle_event(evt)
                if evt.type == KEYDOWN:
                    if evt.key == K_r:
                        self.reload_products_async()

            self.draw()
        pygame.quit()
        sys.exit(0)

    def handle_click_on_grid(self, pos):
        start_x = CARD_MARGIN
        start_y = 80
        x_rel = pos[0] - start_x
        y_rel = pos[1] - start_y + self.scroll
        if x_rel < 0 or y_rel < 0:
            return
        col = x_rel // (CARD_WIDTH + CARD_MARGIN)
        row = y_rel // (CARD_HEIGHT + CARD_MARGIN)
        if col >= COLUMNS:
            return
        idx = int(row * COLUMNS + col)
        if idx < 0 or idx >= len(self.products):
            return
        cx = start_x + col * (CARD_WIDTH + CARD_MARGIN)
        cy = start_y + row * (CARD_HEIGHT + CARD_MARGIN) - self.scroll
        card_rect = pygame.Rect(cx, cy, CARD_WIDTH, CARD_HEIGHT)
        if card_rect.collidepoint(pos):
            product = self.products[idx]
            self.open_edit(product)

    def draw(self):
        self.screen.fill(BG_COLOR)
        pygame.draw.rect(self.screen, (255,255,255), (0,0, SCREEN_SIZE[0], 72))
        title = self.title_font.render("Product Catalog", True, TEXT)
        self.screen.blit(title, (24, 20))
        subtitle = self.small_font.render(f"{self.message}", True, MUTED)
        self.screen.blit(subtitle, (24, 46))

        for b in self.buttons:
            b.draw(self.screen, self.font)

        start_x = CARD_MARGIN
        start_y = 80
        for idx, p in enumerate(self.products):
            col = idx % COLUMNS
            row = idx // COLUMNS
            cx = start_x + col * (CARD_WIDTH + CARD_MARGIN)
            cy = start_y + row * (CARD_HEIGHT + CARD_MARGIN) - self.scroll
            card_rect = pygame.Rect(cx, cy, CARD_WIDTH, CARD_HEIGHT)
            shadow_rect = card_rect.move(4, 6)
            pygame.draw.rect(self.screen, CARD_SHADOW, shadow_rect, border_radius=12)
            pygame.draw.rect(self.screen, CARD_BG, card_rect, border_radius=12)
            pid = p.get("product_id")
            name = p.get("product_name") or ""
            price = p.get("price") or 0
            url = p.get("product_image_url") or ""
            img_rect = pygame.Rect(cx+12, cy+12, CARD_WIDTH-24, 160)
            surf = self.image_surfaces.get(pid) or placeholder_surface((CARD_WIDTH-24, 160))
            self.screen.blit(surf, img_rect.topleft)
            name_txt = self.font.render(name, True, TEXT)
            self.screen.blit(name_txt, (cx+12, cy+12+160+10))
            price_txt = self.title_font.render(f"₹{price}", True, ACCENT)
            self.screen.blit(price_txt, (cx+12, cy+12+160+36))
            edit_btn_rect = pygame.Rect(cx+CARD_WIDTH-12-80, cy+CARD_HEIGHT-48, 80, 34)
            draw_rounded_rect(self.screen, edit_btn_rect, (40, 120, 200), radius=8)
            edit_txt = self.font.render("Edit", True, (255,255,255))
            self.screen.blit(edit_txt, (edit_btn_rect.x + (edit_btn_rect.width - edit_txt.get_width())//2, edit_btn_rect.y + (edit_btn_rect.height - edit_txt.get_height())//2))

        # admin modal
        if self.admin_open:
            overlay = pygame.Surface(SCREEN_SIZE, SRCALPHA)
            overlay.fill((0,0,0,100))
            self.screen.blit(overlay, (0,0))
            draw_rounded_rect(self.screen, self.admin_rect, (255,255,255), radius=12, border=0)
            title = self.title_font.render("Admin — Add / Edit Product", True, TEXT)
            self.screen.blit(title, (self.admin_rect.x + 20, self.admin_rect.y + 18))
            label_font = self.font
            self.screen.blit(label_font.render("Name", True, MUTED), (self.admin_rect.x+24, self.admin_rect.y+46))
            self.screen.blit(label_font.render("Price (INR)", True, MUTED), (self.admin_rect.x+24, self.admin_rect.y+116))
            self.screen.blit(label_font.render("Image URL", True, MUTED), (self.admin_rect.x+24, self.admin_rect.y+186))
            for e in self.form_elements:
                if isinstance(e, InputBox):
                    e.draw(self.screen, self.font)
                else:
                    e.draw(self.screen, self.font)

        pygame.display.flip()

# -------------------
# Main entry
# -------------------
if __name__ == "__main__":
    print(f"Using API at {API_BASE}  (ADMIN_TOKEN={'***' if ADMIN_TOKEN else '(none)'})")
    app = CatalogApp()
    app.run()
