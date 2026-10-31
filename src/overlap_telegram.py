"""Telegram image albums, with no credential URLs exposed in errors."""
import json
from pathlib import Path
import requests
import time


class TelegramMedia:
    def __init__(self,token,chat_id,min_interval=0):
        if not token or not chat_id:raise ValueError('Telegram credentials missing')
        self.token=token;self.chat_id=chat_id
        self.last_receipt={}
        self.min_interval=min_interval;self.last_request=0.

    def pace(self):
        time.sleep(max(0.,self.min_interval-(time.monotonic()-self.last_request)))
        self.last_request=time.monotonic()

    def send_text(self,text):
        self.last_receipt={'outcome':'rejected'}
        if not text or len(text)>4096:return False
        try:
            self.pace();self.last_receipt={'outcome':'unknown'}
            response=requests.post(f'https://api.telegram.org/bot{self.token}/sendMessage',
                                   data={'chat_id':self.chat_id,'text':text},timeout=45)
            result=response.json();ok=response.status_code==200 and result.get('ok') is True
            self.last_receipt=({'outcome':'confirmed','message_ids':[result['result']['message_id']]}
                               if ok else {'outcome':'rejected'})
            return ok
        except (ValueError,KeyError,requests.RequestException):return False

    def send_images(self,items):
        self.last_receipt={'outcome':'rejected'}
        if not 1<=len(items)<=10:return False
        handles=[]
        try:
            for path,caption in items:
                if len(caption)>1024 or not Path(path).is_file():return False
                handles.append(open(path,'rb'))
            # Once a request starts, a missing/invalid response is ambiguous;
            # it must not be treated as proof that Telegram rejected it.
            self.pace()
            self.last_receipt={'outcome':'unknown'}
            if len(items)==1:
                response=requests.post(f'https://api.telegram.org/bot{self.token}/sendPhoto',data={'chat_id':self.chat_id,'caption':items[0][1]},files={'photo':handles[0]},timeout=45)
            else:
                media=[{'type':'photo','media':f'attach://image{i}',**({'caption':caption} if caption else {})} for i,(_,caption) in enumerate(items)]
                response=requests.post(f'https://api.telegram.org/bot{self.token}/sendMediaGroup',data={'chat_id':self.chat_id,'media':json.dumps(media,ensure_ascii=False)},files={f'image{i}':h for i,h in enumerate(handles)},timeout=60)
            result=response.json()
            ok=response.status_code==200 and result.get('ok') is True and (len(items)==1 or len(result.get('result',[]))==len(items))
            if ok:
                messages=[result['result']] if len(items)==1 else result['result']
                self.last_receipt={'outcome':'confirmed','message_ids':[m['message_id'] for m in messages if 'message_id' in m]}
            elif result.get('ok') is True:self.last_receipt={'outcome':'unknown'}
            else:self.last_receipt={'outcome':'rejected'}
            return ok
        except requests.RequestException:
            self.last_receipt={'outcome':'unknown'};return False
        except (OSError,ValueError):return False
        finally:
            for h in handles:h.close()
