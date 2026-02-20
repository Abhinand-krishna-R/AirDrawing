import cv2
import mediapipe as mp
import numpy as np
import time
import math


EMA_ALPHA = 0.2
GESTURE_HOLD_FRAMES = 40
REACQUIRE_DELAY = 0.8


COLORS = [
    (246, 130, 59),  # Sculpt Blue
    (22, 115, 249),  # Action Orange
    (129, 185, 16),  # Emerald
    (255, 255, 255), # Pure White
    (200, 50, 255),  # Neon Purple
    (0, 255, 255),   # Cyber Yellow
    (80, 80, 255),   # Soft Red
    (150, 150, 150)  # Slate Gray
]


# UI HELPERS


def draw_glass_panel(img, pt1, pt2, opacity=0.7):
    overlay = img.copy()
    cv2.rectangle(overlay, pt1, pt2, (15, 15, 15), -1)
    cv2.addWeighted(overlay, opacity, img, 1 - opacity, 0, img)
    cv2.rectangle(img, pt1, pt2, (45, 45, 45), 1)

# ENGINE

class AirSculptPro:
    def __init__(self):
        self.hands = mp.solutions.hands.Hands(
            static_image_mode=False,
            max_num_hands=1,
            min_detection_confidence=0.8,
            min_tracking_confidence=0.8
        )

        self.canvas = None
        self.prev_smooth = None
        self.tracking_state = "LOST"
        self.reacquire_time = 0
        self.clear_buffer = 0
        self.color_index = 0
        self.color_cooldown = 0
        self.mode = "IDLE"
        self.hold_percent = 0

    def get_distance(self, p1, p2):
        return math.sqrt((p1.x - p2.x)**2 + (p1.y - p2.y)**2)

    def is_extended(self, tip_id, wrist, landmarks):
        return self.get_distance(landmarks[tip_id], wrist) > 0.18

    def process_frame(self, frame):
        h, w, _ = frame.shape
        if self.canvas is None or self.canvas.shape != frame.shape:
            self.canvas = np.zeros_like(frame)

        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = self.hands.process(rgb_frame)
        current_time = time.time()
        self.hold_percent = 0

        if not results.multi_hand_landmarks:
            self.tracking_state = "LOST"
            self.mode = "WAITING"
            self.prev_smooth = None
            self.clear_buffer = 0
            return self.render_ui(frame)

        if self.tracking_state == "LOST":
            self.tracking_state = "STABILIZING"
            self.reacquire_time = current_time
            return self.render_ui(frame)

        if self.tracking_state == "STABILIZING":
            if current_time - self.reacquire_time > REACQUIRE_DELAY:
                self.tracking_state = "LOCKED"
            else:
                self.mode = "CALIBRATING"
                return self.render_ui(frame)

        landmarks = results.multi_hand_landmarks[0].landmark
        wrist = landmarks[0]
        index_tip = landmarks[8]

        raw_x, raw_y = int(index_tip.x * w), int(index_tip.y * h)

        if self.prev_smooth is None:
            smooth_x, smooth_y = raw_x, raw_y
        else:
            smooth_x = int(raw_x * EMA_ALPHA + self.prev_smooth[0] * (1 - EMA_ALPHA))
            smooth_y = int(raw_y * EMA_ALPHA + self.prev_smooth[1] * (1 - EMA_ALPHA))

        idx_up = self.is_extended(8, wrist, landmarks)
        mid_up = self.is_extended(12, wrist, landmarks)
        rng_up = self.is_extended(16, wrist, landmarks)
        pky_up = self.is_extended(20, wrist, landmarks)

        # GESTURE LOGIC
        if idx_up and not mid_up:
            self.mode = "SCULPTING"
            if self.prev_smooth:
                cv2.line(self.canvas, self.prev_smooth, (smooth_x, smooth_y), COLORS[self.color_index], 6)
            self.clear_buffer = 0
        elif idx_up and mid_up and not rng_up:
            self.mode = "PALETTE"
            if current_time - self.color_cooldown > 0.6:
                self.color_index = (self.color_index + 1) % len(COLORS)
                self.color_cooldown = current_time
        elif not idx_up and not mid_up and not rng_up:
            self.mode = "CLEARING"
            self.clear_buffer += 1
            self.hold_percent = int((self.clear_buffer / GESTURE_HOLD_FRAMES) * 100)
            if self.clear_buffer >= GESTURE_HOLD_FRAMES:
                self.canvas = np.zeros_like(frame)
                self.clear_buffer = 0
        else:
            self.mode = "IDLE"
            self.clear_buffer = 0

        self.prev_smooth = (smooth_x, smooth_y)
        cv2.circle(frame, (smooth_x, smooth_y), 6, COLORS[self.color_index], -1)
        cv2.circle(frame, (smooth_x, smooth_y), 10, (255, 255, 255), 1)

        return self.render_ui(frame)

    def render_ui(self, frame):
        h, w, _ = frame.shape

        # Blend Canvas
        canvas_gray = cv2.cvtColor(self.canvas, cv2.COLOR_BGR2GRAY)
        _, mask = cv2.threshold(canvas_gray, 1, 255, cv2.THRESH_BINARY)
        mask_inv = cv2.bitwise_not(mask)
        img_bg = cv2.bitwise_and(frame, frame, mask=mask_inv)
        img_fg = cv2.bitwise_and(self.canvas, self.canvas, mask=mask)
        frame = cv2.add(img_bg, img_fg)

        # 1. TOP HEADER
        draw_glass_panel(frame, (0, 0), (w, 50), opacity=0.9)
        status_col = (80, 220, 80) if self.tracking_state == "LOCKED" else (80, 80, 220)
        cv2.circle(frame, (35, 25), 5, status_col, -1)
        cv2.putText(frame, f"SYSTEM {self.tracking_state}", (55, 32), 
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (200, 200, 200), 1, cv2.LINE_AA)
        
        mode_text = f"MODE // {self.mode}"
        t_size = cv2.getTextSize(mode_text, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 2)[0]
        cv2.putText(frame, mode_text, (w//2 - t_size[0]//2, 32), 
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)

        # 2. LEFT SIDEBAR 
        spacing = 45
        panel_h = (len(COLORS) * spacing) + 20
        start_y = h//2 - panel_h//2
        draw_glass_panel(frame, (15, start_y), (75, start_y + panel_h), opacity=0.8)
        for i, col in enumerate(COLORS):
            cy = start_y + 30 + (i * spacing)
            if i == self.color_index:
                cv2.circle(frame, (45, cy), 18, (255, 255, 255), 1, cv2.LINE_AA)
            cv2.circle(frame, (45, cy), 12, col, -1, cv2.LINE_AA)

        # 3. RIGHT SIDEBAR (INSTRUCTIONS)
        instr_w, instr_h = 180, 140
        ix, iy = w - instr_w - 15, h//2 - instr_h//2
        draw_glass_panel(frame, (ix, iy), (w - 15, iy + instr_h), opacity=0.7)
        
        instructions = [
            ("INDEX UP", "SCULPT"),
            ("INDEX+MID", "SWITCH COLOR"),
            ("FIST", "HOLD TO CLEAR")
        ]
        for i, (gest, act) in enumerate(instructions):
            ty = iy + 30 + (i * 30)
            cv2.putText(frame, gest, (ix + 10, ty), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (180, 180, 180), 1, cv2.LINE_AA)
            cv2.putText(frame, act, (ix + 10, ty + 12), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (255, 255, 255), 1, cv2.LINE_AA)

        # 4. ACTION FEEDBACK
        if self.hold_percent > 0:
            bar_w = 200
            bx, by = w//2 - bar_w//2, h - 80
            cv2.rectangle(frame, (bx, by), (bx + bar_w, by + 4), (40, 40, 40), -1)
            fill_w = int((self.hold_percent / 100) * bar_w)
            cv2.rectangle(frame, (bx, by), (bx + fill_w, by + 4), (255, 255, 255), -1)
            cv2.putText(frame, "PURGING CANVAS...", (bx, by - 15), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1, cv2.LINE_AA)

        return frame

# MAIN

if __name__ == "__main__":
    sculptor = AirSculptPro()
    cap = cv2.VideoCapture(0)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

    while cap.isOpened():
        ret, frame = cap.read()
        if not ret: break
        frame = cv2.flip(frame, 1)
        output = sculptor.process_frame(frame)
        cv2.imshow("AirDrawing", output)
        if cv2.waitKey(1) & 0xFF == ord('q'): break

    cap.release()
    cv2.destroyAllWindows()