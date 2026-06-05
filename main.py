import requests
import pytesseract
from PIL import Image, ImageEnhance, ImageFilter
import io
import time
import re
from flask import Flask, request, jsonify
from bs4 import BeautifulSoup

app = Flask(__name__)

# --- TESSERACT CONFIG ---
# pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

def solve_captcha(session):
    try:
        ts = int(time.time() * 1000)
        url = f"https://sarathi.parivahan.gov.in/sarathiservice/jsp/common/captchaimage.jsp?{ts}"
        res = session.get(url, timeout=10)
        img = Image.open(io.BytesIO(res.content)).convert('L')
        img = img.filter(ImageFilter.MedianFilter(size=3))
        img = img.resize((img.width * 4, img.height * 4), Image.Resampling.LANCZOS)
        img = ImageEnhance.Contrast(img).enhance(3.0)
        img = img.point(lambda p: 0 if p < 145 else 255)
        config = r'--oem 3 --psm 7 -c tessedit_char_whitelist=abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
        return re.sub(r'[^a-zA-Z0-9]', '', pytesseract.image_to_string(img, config=config).strip())
    except: return ""

def get_dl_details_remake(dl_no, dob):
    session = requests.Session()
    headers = {
        'Host': 'sarathi.parivahan.gov.in',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:147.0) Gecko/20100101 Firefox/147.0',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Origin': 'https://sarathi.parivahan.gov.in',
        'Referer': 'https://sarathi.parivahan.gov.in/sarathiservice/envaction.do',
        'Connection': 'keep-alive'
    }
    session.headers.update(headers)

    try:
        session.get("https://sarathi.parivahan.gov.in/sarathiservice/stateSelection.do")
        session.post("https://sarathi.parivahan.gov.in/sarathiservice/stateSelectBean.do", data={'stName': 'JH'})

        res = session.get("https://sarathi.parivahan.gov.in/sarathiservice/envaction.do")
        soup = BeautifulSoup(res.text, 'html.parser')
        token_tag = soup.find('input', {'name': 'token'})
        token = token_tag['value'] if token_tag else ""

        for i in range(1, 15):
            captcha = solve_captcha(session)
            if len(captcha) < 5: continue
            
            v_url = "https://sarathi.parivahan.gov.in/sarathiservice/getLastEndorsedRtoDLserReq.do"
            v_data = {'dlno': dl_no, 'dob': dob, 'captchaByApplicant': captcha}
            v_res = session.post(v_url, data=v_data, headers={'X-Requested-With': 'XMLHttpRequest'})
            
            try:
                val_json = v_res.json()
                if val_json and "OK" in str(val_json[1]):
                    st_name = str(val_json[2]).split('@')[0]
                    appl_name = str(val_json[2]).split('@')[1] if '@' in str(val_json[2]) else ""
                    rto_name = val_json[3]

                    fields = {
                        'dlno': (None, dl_no), 'dob': (None, dob), 'entCaptha': (None, captcha),
                        'PrivacyPolicyTermsofService': (None, 'true'),
                        '__checkbox_PrivacyPolicyTermsofService': (None, 'true'),
                        'dispDLDet': (None, 'YES'), 'applcatgDLserReq': (None, 'General'),
                        'stateCodeDLTr': (None, 'Jharkhand'), 'rtoCodeDLTr': (None, '-1'),
                        'struts.token.name': (None, 'token'), 'token': (None, token),
                        'reset': (None, 'formsubmit'), 'stEndName': (None, st_name),
                        'rtoEndName': (None, rto_name), 'ApplFullNameDLSReq': (None, appl_name)
                    }
                    
                    final_res = session.post("https://sarathi.parivahan.gov.in/sarathiservice/envaction.do", files=fields)
                    return parse_everything(final_res.text, dl_no, dob)
            except: pass
            time.sleep(0.2)

    except Exception as e: return {"error": str(e)}
    return {"status": 404, "message": "Failed after max retries"}

def parse_everything(html, dl_no, dob):
    soup = BeautifulSoup(html, 'html.parser')
    
    # Helper to find text by label
    def get_table_val(label):
        node = soup.find(string=re.compile(label, re.I))
        if node:
            parent_td = node.find_parent('td')
            if parent_td:
                sibling = parent_td.find_next_sibling('td')
                return sibling.text.strip() if sibling else "N/A"
        return "N/A"

    # 1. Address Parsing (Multi-line)
    address_parts = []
    addr_start = soup.find('td', string=re.compile("Present Address:", re.I))
    if addr_start:
        first_row = addr_start.find_parent('tr')
        address_parts.append(first_row.find_all('td')[-1].text.strip())
        # Check subsequent rows where first td is empty
        for row in first_row.find_next_siblings('tr'):
            tds = row.find_all('td')
            if len(tds) == 2 and not tds[0].text.strip():
                val = tds[1].text.strip()
                if val: address_parts.append(val)
            else:
                break

    # 2. Last Endorsed Details (State & RTO)
    endorsed_state = ""
    endorsed_rto = ""
    state_b = soup.find('b', string=re.compile("State-", re.I))
    if state_b: endorsed_state = state_b.next_sibling.strip()
    rto_b = soup.find('b', string=re.compile("RTO  -", re.I))
    if rto_b: endorsed_rto = rto_b.next_sibling.strip()

    # 3. Class of Vehicles (COV)
    cov_list = []
    cov_legend = soup.find('legend', string=re.compile("Class of Vehicles", re.I))
    if cov_legend:
        cov_table = cov_legend.find_parent('fieldset').find('table')
        if cov_table:
            for row in cov_table.find_all('tr')[1:]: # Skip header
                cols = row.find_all('td')
                if len(cols) >= 2:
                    cov_list.append({
                        "cov_abbr": cols[0].text.strip(),
                        "authority": cols[1].text.strip()
                    })

    # 4. Validity
    validity_nt = "N/A"
    nt_label = soup.find(id="envaction_dl_nt") or soup.find(string=re.compile("Non - Transport", re.I))
    if nt_label:
        v_parent = nt_label.find_parent('div').find_next_sibling('div', class_='text-center')
        if v_parent: validity_nt = v_parent.text.strip()

    # 5. Mobile & Email
    email_tag = soup.find('input', attrs={'name': 'emailID'})
    mobile_tag = soup.find('input', attrs={'name': 'mobileno'})

    return {
        "status": 200,
        "data": {
            "personal_info": {
                "name": get_table_val("Name :"),
                "father_name": get_table_val("Father's Name :"),
                "dob": dob,
                "email": email_tag.get('value') if email_tag else "N/A",
                "mobile": mobile_tag.get('value') if mobile_tag else "N/A",
                "address": ", ".join(address_parts)
            },
            "dl_details": {
                "dl_number": dl_no,
                "last_endorsed_state": endorsed_state,
                "last_endorsed_rto": endorsed_rto,
                "cov_details": cov_list,
                "validity_non_transport": validity_nt,
                "photo": soup.find('input', {'name': 'imgHid'})['value'] if soup.find('input', {'name': 'imgHid'}) else None,
                "signature": soup.find('input', {'name': 'sigHid'})['value'] if soup.find('input', {'name': 'sigHid'}) else None
            }
        }
    }

@app.route('/get-dl', methods=['GET'])
def api():
    dlno = request.args.get('dlno')
    dob = request.args.get('dob')
    if not dlno or not dob: return jsonify({"error": "Params missing"}), 400
    return jsonify(get_dl_details_remake(dlno, dob))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)