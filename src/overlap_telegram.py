"""Telegram image albums, with no credential URLs exposed in errors."""
import json
from pathlib import Path
import requests


class TelegramMedia:
    def __init__(self,token,chat_id):
        if not token or not chat_id:raise ValueError('Telegram credentials missing')
        self.token=token;self.chat_id=chat_id

    def send_images(self,items):
        if not 1<=len(items)<=10:return False
        handles=[]
        try:
            for path,caption in items:
                if len(caption)>1024 or not Path(path).is_file():return False
                handles.append(open(path,'rb'))
            if len(items)==1:
                response=requests.post(f'https://api.telegram.org/bot{self.token}/sendPhoto',data={'chat_id':self.chat_id,'caption':items[0][1]},files={'photo':handles[0]},timeout=45)
            else:
                media=[{'type':'photo','media':f'attach://image{i}',**({'caption':caption} if caption else {})} for i,(_,caption) in enumerate(items)]
                response=requests.post(f'https://api.telegram.org/bot{self.token}/sendMediaGroup',data={'chat_id':self.chat_id,'media':json.dumps(media,ensure_ascii=False)},files={f'image{i}':h for i,h in enumerate(handles)},timeout=60)
            result=response.json()
            return response.status_code==200 and result.get('ok') is True and (len(items)==1 or len(result.get('result',[]))==len(items))
        except (OSError,ValueError,requests.RequestException):return False
        finally:
            for h in handles:h.close()
