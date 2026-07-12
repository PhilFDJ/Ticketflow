"""
Verify qrgen produces valid, scannable QR codes.

Oracle: OpenCV's QRCodeDetectorAruco (a strong, standards-compliant locator
comparable to what phone cameras and the browser BarcodeDetector use).
The older cv2.QRCodeDetector is a weak locator and is NOT used here.

Run: python3 tests/test_qr.py
"""
import sys, os, base64, secrets, random, string
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import numpy as np
import cv2
import qrgen


def render(text, level, scale=12, quiet=4):
    matrix = qrgen.make_matrix(text, level)
    n = len(matrix)
    total = n + quiet * 2
    img = np.full((total, total), 255, dtype=np.uint8)
    for r in range(n):
        for c in range(n):
            if matrix[r][c]:
                img[r + quiet][c + quiet] = 0
    return cv2.resize(img, (total * scale, total * scale),
                      interpolation=cv2.INTER_NEAREST)


def make_detector():
    return cv2.QRCodeDetectorAruco()


def decode(det, text, level):
    data, _, _ = det.detectAndDecode(render(text, level))
    return data


def run():
    det = make_detector()
    passed = 0
    total = 0
    failures = []

    # 1. Realistic ticket-code format, the ONLY thing the app actually encodes.
    #    Matches app.tokens.ticket_code(): "TKT-" + 26-char base32 token.
    for _ in range(2000):
        code = "TKT-" + base64.b32encode(secrets.token_bytes(16)).decode().rstrip("=")
        total += 1
        if decode(det, code, "Q") == code:
            passed += 1
        else:
            failures.append(("ticket", len(code), code))

    # 2. General strings of varied length/charset at multiple ECC levels.
    random.seed(123)
    alphabet = string.ascii_letters + string.digits + "-_:./?=&%+ "
    for level in ("L", "M", "Q", "H"):
        for length in [1, 5, 12, 20, 32, 44, 60, 80, 110]:
            for _ in range(6):
                text = "".join(random.choice(alphabet) for _ in range(length))
                try:
                    got = decode(det, text, level)
                except ValueError:
                    # beyond supported capacity for this level — skip
                    continue
                total += 1
                if got == text:
                    passed += 1
                else:
                    failures.append((level, length, text))

    print(f"PASSED {passed}/{total}")
    for lvl, ln, txt in failures[:25]:
        print(f"  FAIL [{lvl}] len={ln} {txt!r}")
    return len(failures) == 0


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
