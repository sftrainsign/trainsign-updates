import os, time, json, wifi, socketpool, board, displayio
import framebufferio, rgbmatrix, terminalio, rtc
import adafruit_ntp
from adafruit_bitmap_font import bitmap_font
from adafruit_display_text import label
import math

displayio.release_displays()

matrix = rgbmatrix.RGBMatrix(
    width=64, height=32, bit_depth=4,
    rgb_pins=[board.MTX_R1, board.MTX_B1, board.MTX_G1,
              board.MTX_R2, board.MTX_B2, board.MTX_G2],
    addr_pins=[board.MTX_ADDRA, board.MTX_ADDRB,
               board.MTX_ADDRC, board.MTX_ADDRD],
    clock_pin=board.MTX_CLK, latch_pin=board.MTX_LAT,
    output_enable_pin=board.MTX_OE)
display = framebufferio.FramebufferDisplay(matrix, auto_refresh=True)
print("Connecting to WiFi...")
wifi.radio.connect(os.getenv("CIRCUITPY_WIFI_SSID"), os.getenv("CIRCUITPY_WIFI_PASSWORD"))
print("Connected:", wifi.radio.ipv4_address)

pool = socketpool.SocketPool(wifi.radio)
ntp = adafruit_ntp.NTP(pool, tz_offset=0, server="time.cloudflare.com")
rtc.RTC().datetime = ntp.datetime
print("Time synced")
small_font = bitmap_font.load_font("/MatrixChunky8.bdf")

API_KEY   = os.getenv("TRANSIT_511_API_KEY")
STOP_CODE = "13915"
AGENCY    = "SF"
POLL_SECS = 30

MUNI_BLUE = 0x0000FF
AMBER     = 0xFF6600
GREEN     = 0x00FF00
RED       = 0xFF2020
WHITE     = 0xFFFFFF
BLACK     = 0x000000
DIM       = 0x888888

def draw_circle_logo(group, x, y):
    bmp = displayio.Bitmap(13, 13, 3)
    pal = displayio.Palette(3)
    pal[0] = BLACK
    pal[1] = MUNI_BLUE
    pal[2] = WHITE
    cx, cy, r = 6.0, 6.0, 5.8
    for row in range(13):
        for col in range(13):
            dist = math.sqrt((col - cx)**2 + (row - cy)**2)
            if dist <= r:
                bmp[col, row] = 1
    N = [
        (3,3),(3,4),(3,5),(3,6),(3,7),(3,8),(3,9),
        (9,3),(9,4),(9,5),(9,6),(9,7),(9,8),(9,9),
        (4,4),(5,5),(6,6),(7,7),(8,8),
    ]
    for col, row in N:
        bmp[col, row] = 2
    group.append(displayio.TileGrid(bmp, pixel_shader=pal, x=x, y=y))

def http_get(host, path):
    addr = pool.getaddrinfo(host, 80)[0][4]
    sock = pool.socket()
    sock.settimeout(10)
    sock.connect(addr)
    req = "GET {} HTTP/1.0\r\nHost: {}\r\nAccept-Encoding: identity\r\nConnection: close\r\n\r\n".format(path, host)
    sock.send(req.encode())
    response = b""
    buf = bytearray(2048)
    while True:
        try:
            n = sock.recv_into(buf)
            if n == 0:
                break
            response += buf[:n]
        except:
            break
    sock.close()
    if b"\r\n\r\n" not in response:
        return ""
    body = response.split(b"\r\n\r\n", 1)[1]
    if body[:3] == b"\xef\xbb\xbf":
        body = body[3:]
    return body.decode("utf-8", "ignore")

def fetch_arrivals():
    path = "/transit/StopMonitoring?api_key={}&agency={}&stopCode={}&format=json".format(
        API_KEY, AGENCY, STOP_CODE)
    try:
        body = http_get("api.511.org", path)
        data = json.loads(body)
        delivery = data["ServiceDelivery"]["StopMonitoringDelivery"]
        if isinstance(delivery, list):
            delivery = delivery[0]
        visits = delivery.get("MonitoredStopVisit", [])
        arrivals = []
        for visit in visits:
            call = visit["MonitoredVehicleJourney"].get("MonitoredCall", {})
            exp = (call.get("ExpectedArrivalTime") or
                   call.get("ExpectedDepartureTime") or
                   call.get("AimedArrivalTime"))
            if exp:
                t = exp[11:19]
                h, m = int(t[0:2]), int(t[3:5])
                now = time.localtime()
                diff = (h * 60 + m) - (now.tm_hour * 60 + now.tm_min)
                if diff < 0:
                    diff += 1440
                arrivals.append(max(0, diff))
            if len(arrivals) >= 2:
                break
        return arrivals
    except Exception as e:
        print("Error:", e)
        return []

def make_display(arrivals):
    group = displayio.Group()
    stop_text = "Carl/Stanyan"
    stop_lbl = label.Label(small_font, text=stop_text, color=AMBER, x=0, y=5)
    stop_w = stop_lbl.bounding_box[2]
    stop_lbl.x = max(0, (64 - stop_w) // 2)
    group.append(stop_lbl)
    div = displayio.Bitmap(64, 1, 1)
    div_pal = displayio.Palette(1)
    div_pal[0] = 0x222222
    group.append(displayio.TileGrid(div, pixel_shader=div_pal, x=0, y=10))
    draw_circle_logo(group, 1, 14)
    caltrain_lbl = label.Label(small_font, text="Caltrain", color=DIM, x=0, y=17)
    caltrain_w = caltrain_lbl.bounding_box[2]
    caltrain_lbl.x = 17 + max(0, (40 - caltrain_w) // 2)
    group.append(caltrain_lbl)
    if not arrivals:
        group.append(label.Label(small_font, text="No data", color=RED, x=17, y=26))
    else:
        times = ["Now" if m == 0 else "{}m".format(m) for m in arrivals[:2]]
        times_str = "  ".join(times)
        times_lbl = label.Label(small_font, text=times_str, color=GREEN, x=0, y=26)
        times_w = times_lbl.bounding_box[2]
        times_lbl.x = 17 + max(0, (40 - times_w) // 2)
        group.append(times_lbl)
    return group

display.root_group = make_display([])

arrivals = []
last_fetch = 0

while True:
    now_t = time.monotonic()
    if now_t - last_fetch >= POLL_SECS:
        arrivals = fetch_arrivals()
        last_fetch = now_t
        display.root_group = make_display(arrivals)
    time.sleep(1)
