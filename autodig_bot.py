import cv2
import numpy as np
import mss
import pynput
import pygetwindow as gw
import time
import threading # For keyboard listener

# --- Configuration ---
# User will need to calibrate these values
CONFIG = {
    "ROBLOX_WINDOW_TITLE": "Roblox",  # Default Roblox window title
    "ROI_X_OFFSET": 100,  # Pixels from left of Roblox window to start of ROI
    "ROI_Y_OFFSET": 200,  # Pixels from top of Roblox window to start of ROI
    "ROI_WIDTH": 600,     # Width of the ROI
    "ROI_HEIGHT": 100,    # Height of the ROI

    # HSV Color Ranges - These MUST be calibrated by the user
    # Format: (Lower_Bound_HSV, Upper_Bound_HSV)
    # Each bound is (Hue, Saturation, Value)
    # Hue: 0-179 (in OpenCV), Saturation: 0-255, Value: 0-255
    "CURSOR_COLOR_RANGE_HSV": {
        "lower": (0, 0, 0),    # Example: Blackish cursor (NEEDS CALIBRATION)
        "upper": (179, 255, 50) # (NEEDS CALIBRATION)
    },
    "TARGET_ZONE_COLOR_RANGE_HSV": { # Dark Brown Zone
        "lower": (10, 100, 20), # Example: Brownish (NEEDS CALIBRATION)
        "upper": (20, 255, 200) # (NEEDS CALIBRATION)
    },

    "LOOP_DELAY_SECONDS": 0.01, # Delay between screen captures/processing cycles
    "CLICK_REACTION_TIME_BUFFER_SECONDS": 0.05, # Time to predict cursor forward
    "DEBUG_MODE": True, # If True, will show processed frames and print debug info
    "EXIT_KEY_ASCII": 27 # ASCII for ESC key to stop the bot
}

# --- Global Variables ---
mouse_controller = pynput.mouse.Controller()
keyboard_controller = pynput.keyboard.Controller()
running = True # Controls the main loop and listener thread

# --- Function Definitions ---

def find_roblox_window():
    """Finds the Roblox game window."""
    print_debug(f"Attempting to find Roblox window with title: '{CONFIG['ROBLOX_WINDOW_TITLE']}'")
    try:
        windows = gw.getWindowsWithTitle(CONFIG["ROBLOX_WINDOW_TITLE"])
        if windows:
            # Filter out minimized windows or windows with no size, if possible
            valid_windows = [w for w in windows if w.width > 0 and w.height > 0 and not w.isMinimized]
            if not valid_windows:
                print_debug("Roblox window found, but it might be minimized or have no size.")
                # Try to activate/restore if needed, though pygetwindow has limited capability here.
                # For now, just return the first one if any original windows were found.
                if windows:
                    roblox_win = windows[0]
                    print_debug(f"Using first found Roblox window (may be minimized): '{roblox_win.title}'")
                    return roblox_win
                return None

            roblox_win = valid_windows[0]  # Assume the first valid one
            # Optional: If multiple valid windows, could add logic to select the active one or largest one.
            # roblox_win.activate() # This might be disruptive or not work consistently depending on OS and permissions.

            print_debug(f"Roblox window found: '{roblox_win.title}', Geom: ({roblox_win.left}, {roblox_win.top}, {roblox_win.width}, {roblox_win.height}), Active: {roblox_win.isActive}")
            return roblox_win
        else:
            print_debug("Roblox window not found.")
            return None
    except Exception as e:
        print_debug(f"Error finding Roblox window: {e}")
        return None

def capture_roi(roblox_window_obj, roi_config):
    """
    Captures the specified Region of Interest (ROI) from the screen,
    relative to the provided Roblox window object.
    Returns a NumPy array (BGR format for OpenCV) or None if capture fails.
    """
    if not roblox_window_obj:
        print_debug("capture_roi: Roblox window object is None.")
        return None

    # ROI coordinates are relative to the Roblox window's content area
    # Note: roblox_window_obj.left and .top usually refer to the top-left of the window including title bar.
    # For content area, adjustments might be needed if title bar/borders are thick, but often this is close enough.
    roi_screen_x = roblox_window_obj.left + roi_config["ROI_X_OFFSET"]
    roi_screen_y = roblox_window_obj.top + roi_config["ROI_Y_OFFSET"]

    # Ensure ROI is within screen bounds (basic check)
    # More robust checks would involve screen dimensions from mss.monitors
    if roi_config["ROI_WIDTH"] <= 0 or roi_config["ROI_HEIGHT"] <= 0:
        print_debug("capture_roi: ROI width or height is zero or negative.")
        return None

    monitor = {
        "top": roi_screen_y,
        "left": roi_screen_x,
        "width": roi_config["ROI_WIDTH"],
        "height": roi_config["ROI_HEIGHT"],
    }

    try:
        with mss.mss() as sct:
            sct_img = sct.grab(monitor)

        # Convert to NumPy array and then to BGR format for OpenCV
        img = np.array(sct_img)
        img_bgr = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR) # MSS captures BGRA

        # print_debug(f"ROI captured successfully. Shape: {img_bgr.shape}")
        return img_bgr
    except mss.exception.ScreenShotError as e:
        print_debug(f"Error capturing ROI with MSS: {e}")
        print_debug(f"Attempted monitor details: {monitor}")
        # This can happen if ROI is off-screen, or window minimized mid-capture etc.
        return None
    except Exception as e:
        print_debug(f"Unexpected error in capture_roi: {e}")
        return None

def find_element_in_roi(image_roi, color_range_hsv, element_name="Element"):
    """
    Finds an element (cursor or target zone) within the ROI based on HSV color range.
    For 'Cursor', returns its center X-coordinate.
    For 'TargetZone', returns a tuple (X_start, X_end) of its horizontal span.
    Returns None if the element is not found.
    """
    if image_roi is None:
        print_debug(f"find_element_in_roi: image_roi is None for {element_name}.")
        return None

    hsv_image = cv2.cvtColor(image_roi, cv2.COLOR_BGR2HSV)

    lower_bound = np.array(color_range_hsv["lower"])
    upper_bound = np.array(color_range_hsv["upper"])

    mask = cv2.inRange(hsv_image, lower_bound, upper_bound)

    # Optional: Apply morphological operations to clean up the mask
    # kernel = np.ones((3,3), np.uint8)
    # mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel) # Remove small noise
    # mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel) # Fill small holes

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    if not contours:
        # print_debug(f"No contours found for {element_name} with color range: {color_range_hsv}")
        return None

    if element_name == "Cursor":
        # Assume the largest contour or a specific shaped contour is the cursor
        # For simplicity, let's take the largest contour by area
        largest_contour = max(contours, key=cv2.contourArea)
        M = cv2.moments(largest_contour)
        if M["m00"] == 0: # Avoid division by zero
            print_debug(f"Cursor contour found for {element_name} but has zero area.")
            return None
        center_x = int(M["m10"] / M["m00"])
        # print_debug(f"Cursor found at X: {center_x}")
        if CONFIG["DEBUG_MODE"]: # Draw on the original ROI for visualization
            cv2.drawContours(image_roi, [largest_contour], -1, (0,255,0), 1) # Green
            cv2.circle(image_roi, (center_x, image_roi.shape[0] // 2), 3, (0,0,255), -1) # Red dot
        return center_x

    elif element_name == "TargetZone":
        # Assume the target zone is also a significant contour.
        # If multiple contours, could combine them or take the largest.
        # For simplicity, find min X and max X among all found contour points for the zone.
        if not contours: return None

        all_points = np.concatenate(contours)
        x_start = int(all_points[:, :, 0].min())
        x_end = int(all_points[:, :, 0].max())

        # print_debug(f"Target Zone found from X: {x_start} to X: {x_end}")
        if CONFIG["DEBUG_MODE"]: # Draw on the original ROI for visualization
            # Draw all contours that made up the zone
            cv2.drawContours(image_roi, contours, -1, (255,0,0), 1) # Blue
            # Draw a rectangle representing the combined extent
            cv2.rectangle(image_roi, (x_start, 0), (x_end, image_roi.shape[0]-1), (255,255,0), 1) # Cyan
        return (x_start, x_end)

    return None


def perform_click(screen_x, screen_y):
    """Performs a mouse click at the given screen coordinates."""
    # Placeholder - to be implemented in Step 5
    print_debug(f"Attempting click at screen coordinates: ({screen_x:.0f}, {screen_y:.0f})")
    try:
        original_pos = mouse_controller.position
        mouse_controller.position = (int(screen_x), int(screen_y))
        time.sleep(0.01) # Brief pause before click, can sometimes help
        mouse_controller.click(pynput.mouse.Button.left, 1)
        time.sleep(0.02) # Brief pause after click
        # Optional: Restore mouse position
        # mouse_controller.position = original_pos
        print_debug(f"Click performed at ({screen_x:.0f}, {screen_y:.0f}).")
    except Exception as e:
        print_debug(f"Error during perform_click: {e}")


def print_debug(message):
    """Prints message if DEBUG_MODE is True."""
    if CONFIG["DEBUG_MODE"]:
        print(f"[DEBUG] {time.strftime('%H:%M:%S')} - {message}")

def on_press(key):
    """Handles key presses for the keyboard listener."""
    global running
    try:
        # Check for ESC key (ASCII 27) or specific key combination
        if hasattr(key, 'vk') and key.vk == CONFIG["EXIT_KEY_ASCII"]: # For keys like Esc
             print_debug("Exit key pressed. Stopping bot.")
             running = False
             return False # Stop listener
        elif hasattr(key, 'char') and key.char == 'q': # Example: 'q' to quit
             print_debug("'q' key pressed. Stopping bot.")
             running = False
             return False # Stop listener

    except AttributeError:
        pass # Ignore special keys that don't have 'char' or 'vk'

def start_keyboard_listener():
    """Starts the keyboard listener in a separate thread."""
    global running
    running = True # Ensure running is true when listener starts
    print_debug(f"Starting keyboard listener. Press 'ESC' (or 'q') to stop the bot.")
    listener = pynput.keyboard.Listener(on_press=on_press)
    listener.start()
    return listener

def main_loop():
    """Main operational loop for the bot."""
    global running
    print_debug("Bot main_loop started.")

    listener_thread = start_keyboard_listener()

    # --- Variables for prediction ---
    prev_cursor_x_roi = None
    prev_time = time.time()

    roblox_win = None

    while running:
        current_time = time.time()
        delta_time = current_time - prev_time
        prev_time = current_time

        if not roblox_win or not roblox_win.exists or roblox_win.isMinimized: # Check if window exists and is not minimized
            roblox_win = find_roblox_window() # Attempt to re-find it
            if not roblox_win or not roblox_win.exists or roblox_win.isMinimized:
                print_debug("Roblox window lost, not found, or minimized. Waiting...")
                time.sleep(1)
                prev_cursor_x_roi = None # Reset prediction state
                if roblox_win and not roblox_win.isActive : print_debug("Hint: Roblox window might also not be the active (focused) window.")
                continue

        # Check if window is active (focused); clicks might not register if not.
        # This is a common issue for pixel bots.
        if not roblox_win.isActive:
            print_debug("Roblox window is not active (focused). Please click on the Roblox window to focus it.")
            # Optionally, try to activate it, but this can be disruptive or fail.
            # try:
            #     roblox_win.activate()
            #     print_debug("Attempted to activate Roblox window.")
            #     time.sleep(0.5) # Give time for activation
            #     if not roblox_win.isActive:
            #         print_debug("Activation might have failed. Please manually focus the window.")
            # except Exception as e:
            #     print_debug(f"Error during window activation attempt: {e}")
            time.sleep(1)
            prev_cursor_x_roi = None # Reset prediction state
            continue

        # --- Capture ROI ---
        roi_image = capture_roi(roblox_win, CONFIG)
        if roi_image is None:
            print_debug("Failed to capture ROI. Skipping this frame.")
            if CONFIG["DEBUG_MODE"] and cv2.getWindowProperty("ROI View", cv2.WND_PROP_VISIBLE) >= 1: # Check if window was closed
                pass # Keep trying to show it later if debug is on
            elif CONFIG["DEBUG_MODE"]: # Window not open yet or never opened
                pass
            else: # Not debug mode, just sleep
                time.sleep(CONFIG["LOOP_DELAY_SECONDS"])
            prev_cursor_x_roi = None
            continue

        # --- Find Cursor ---
        cursor_x_roi = find_element_in_roi(roi_image, CONFIG["CURSOR_COLOR_RANGE_HSV"], "Cursor")

        # --- Find Target Zone ---
        target_zone_x_roi_tuple = find_element_in_roi(roi_image, CONFIG["TARGET_ZONE_COLOR_RANGE_HSV"], "TargetZone") # (x_start, x_end)

        # --- Prediction & Click Logic ---
        if cursor_x_roi is not None and target_zone_x_roi_tuple is not None:
            cursor_speed_pps = 0 # Pixels per second in ROI coordinates
            if prev_cursor_x_roi is not None and delta_time > 0:
                # Ensure delta_time is not excessively small to prevent huge speed values
                if delta_time > 0.001: # Minimum sensible delta_time
                    cursor_speed_pps = (cursor_x_roi - prev_cursor_x_roi) / delta_time

            predicted_cursor_x_roi = cursor_x_roi + cursor_speed_pps * CONFIG["CLICK_REACTION_TIME_BUFFER_SECONDS"]

            # Store current cursor_x_roi for next frame's speed calculation BEFORE potential early exit
            prev_cursor_x_roi = cursor_x_roi

            target_x_start_roi, target_x_end_roi = target_zone_x_roi_tuple

            # Check if the predicted cursor position is within the target zone
            if target_x_start_roi <= predicted_cursor_x_roi <= target_x_end_roi:
                # Calculate absolute screen coordinates for the click
                # Click in the middle of the detected target zone, or at the predicted cursor spot
                click_target_x_in_roi = (target_x_start_roi + target_x_end_roi) / 2
                # click_target_x_in_roi = predicted_cursor_x_roi # Alternative: click where cursor is predicted to be

                click_screen_x = roblox_win.left + CONFIG["ROI_X_OFFSET"] + click_target_x_in_roi
                click_screen_y = roblox_win.top + CONFIG["ROI_Y_OFFSET"] + (CONFIG["ROI_HEIGHT"] / 2) # Click vertically in middle of ROI

                perform_click(click_screen_x, click_screen_y)
                print_debug(f"Predicted HIT & CLICK! CursorNow: {cursor_x_roi:.0f}, Speed: {cursor_speed_pps:.0f} pps, PredictedROI_X: {predicted_cursor_x_roi:.0f}, TargetZoneROI: [{target_x_start_roi:.0f}-{target_x_end_roi:.0f}]")

                # Optional: Brief pause after a click to prevent immediate re-clicks if conditions are met for too long
                # This might be game-dependent. If the target zone moves quickly, this might not be needed.
                time.sleep(0.1) # e.g., 100ms pause after a click attempt
                prev_cursor_x_roi = None # Reset prediction after a click to avoid using stale speed on new target
            else:
                 print_debug(f"Prediction MISS. CursorNow: {cursor_x_roi:.0f}, Speed: {cursor_speed_pps:.0f} pps, PredictedROI_X: {predicted_cursor_x_roi:.0f}, TargetZoneROI: [{target_x_start_roi:.0f}-{target_x_end_roi:.0f}]")

        else: # One or both elements not found
            prev_cursor_x_roi = None # Reset prediction state if cursor or target is lost
            if cursor_x_roi is None: print_debug("Cursor not found this frame.")
            if target_zone_x_roi_tuple is None: print_debug("Target zone not found this frame.")


        # --- Debug Display (Optional) ---
        if CONFIG["DEBUG_MODE"] and roi_image is not None:
           # roi_image_display = roi_image.copy() # Work on a copy if find_element_in_roi doesn't draw directly
           # If find_element_in_roi draws, roi_image already has debug drawings
           cv2.imshow("ROI View - Press 'q' in this window to quit", roi_image)
           # Key press handling for the OpenCV window
           key_cv = cv2.waitKey(1) & 0xFF
           if key_cv == ord('q'):
               print_debug("'q' pressed in ROI View window. Stopping bot.")
               running = False
           elif key_cv == ord('p'): # Example: Pause functionality
               print_debug("Pausing bot. Press 'p' again in ROI View to resume.")
               while True:
                   if cv2.waitKey(0) & 0xFF == ord('p'):
                       print_debug("Resuming bot.")
                       break
                   if not (cv2.getWindowProperty("ROI View", cv2.WND_PROP_VISIBLE) >= 1): # if window closed
                       running = False
                       break


        if not running: # Check running flag again in case listener or debug window changed it
            break

        time.sleep(CONFIG["LOOP_DELAY_SECONDS"])

    print_debug("Main loop ended.")
    # Listener thread should stop automatically as 'running' is False and its on_press returns False
    if listener_thread.is_alive():
        print_debug("Waiting for listener thread to join...")
        listener_thread.join(timeout=1.0) # Wait for the thread to finish
        if listener_thread.is_alive():
            print_debug("Listener thread did not join. Forcing pynput listener stop (experimental).")
            # This is a bit of a hack; ideally, the listener stops itself.
            # pynput.keyboard.Listener.stop(listener_thread) # This is not the correct way to stop a specific instance

    cv2.destroyAllWindows() # Clean up any OpenCV windows

# --- Entry Point ---
if __name__ == "__main__":
    print("Auto-Dig Bot Script Initializing...")
    print(f"IMPORTANT: Ensure Roblox window is titled '{CONFIG['ROBLOX_WINDOW_TITLE']}'.")
    print("IMPORTANT: ROI and Color Ranges in CONFIG section MUST be calibrated for your specific game and screen setup.")
    print(f"Press 'ESC' (or 'q' if configured) in the terminal or on the ROI window (if shown) to stop the bot.")

    # A small delay to allow user to switch to Roblox window if needed,
    # or to read messages, before main_loop starts if it were fully active.
    # For now, main_loop has placeholders so it's less critical.
    # time.sleep(3)

    main_loop()

    print("Bot script has terminated.")
