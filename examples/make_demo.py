"""Generate a tiny, synthetic Persian document import; no real user data."""
import io,json,zipfile
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
root=Path(__file__).resolve().parents[1]
font=ImageFont.truetype(str(root/'app/static/Vazirmatn.ttf'),36)
image=Image.new('RGB',(800,1050),'#fffdf5');draw=ImageDraw.Draw(image)
draw.text((740,110),'سلام دنیا',font=font,fill='#20241f',anchor='ra',direction='rtl')
draw.text((740,210),'این یک صفحهٔ نمونه برای بازبینی است.',font=font,fill='#20241f',anchor='ra',direction='rtl')
b=io.BytesIO();image.save(b,'PNG')
record={'id':'demo-page-1','image':'demo.png','boxes':[{'x0':510,'y0':90,'x1':760,'y1':170,'label':'Title','text':'سلام دنیا'},{'x0':100,'y0':190,'x1':760,'y1':270,'label':'Text','text':'این یک صفحهٔ نمونه برای بازبینی است.'}]}
with zipfile.ZipFile(root/'examples/demo.zip','w',zipfile.ZIP_DEFLATED) as z:
 z.writestr('records.jsonl',json.dumps(record,ensure_ascii=False)+'\n');z.writestr('demo.png',b.getvalue())
print('Generated examples/demo.zip')
