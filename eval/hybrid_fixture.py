"""Known-label engineering test sheet; synthetic scores are not real-drawing accuracy."""
import io
from pathlib import Path
import hashlib
import json

from PIL import Image, ImageDraw, ImageFont


def create(folder):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    image=Image.new("RGB",(1300,900),"white");draw=ImageDraw.Draw(image)
    font=ImageFont.truetype("C:/Windows/Fonts/arial.ttf",28)
    small=ImageFont.truetype("C:/Windows/Fonts/arial.ttf",24)
    labels=[]
    def text(position,value,measurement=False):
        draw.text(position,value,font=font,fill="black")
        if measurement: labels.append(value)
    def line(points): draw.line(points,fill="black",width=2)
    text((70,20),"TEST PLATE")
    draw.rectangle((150,130,650,430),outline="black",width=3)
    for x in (250,550):
        for y in (205,355):
            draw.ellipse((x-25,y-25,x+25,y+25),outline="black",width=3)
            line((x-35,y,x+35,y));line((x,y-35,x,y+35))
    line((150,75,650,75));line((150,65,150,120));line((650,65,650,120))
    text((325,42),"100 ±0.1",True)
    line((95,130,95,430));line((85,130,140,130));line((85,430,140,430))
    text((35,270),"60",True)
    text((730,170),"4xØ10 THRU",True);line((720,205,575,205))
    line((150,475,250,475));line((150,440,150,485));line((250,390,250,485))
    text((175,485),"20 ±0.2",True)
    line((710,130,710,205));line((660,130,720,130));line((585,205,720,205))
    text((735,120),"15",True)
    text((310,525),"PLAN VIEW")
    for x in range(120,690,25):
        line((x,205,min(x+12,690),205))
    text((110,210),"A");text((665,210),"A")
    # Source section: cut material on either side of the two through holes.
    for left,right in ((150,225),(275,525),(575,650)):
        draw.rectangle((left,650,right,710),outline="black",width=2)
        for x in range(left+5,right,14):
            line((x,710,min(x+40,right),max(650,710-(right-x))))
    for x in (225,275,525,575): line((x,650,x,710))
    line((700,650,700,710));line((660,650,710,650));line((660,710,710,710))
    text((725,662),"12",True)
    text((300,740),"SECTION A-A")
    text((70,830),"UNITS: mm    THIRD ANGLE")
    draw.text((760,760),"ALL HOLES THROUGH",font=small,fill="black")
    buffer=io.BytesIO();image.save(buffer,"PNG");png=buffer.getvalue()
    path=folder/"hybrid_test_plate.png";path.write_bytes(png)
    reference={"source_sha":hashlib.sha256(png).hexdigest(),"verification":"machine_generated_labels",
               "scope":"All six generated measurement callouts, two view count, printed units and projection; no CAD reconstruction certification",
               "measurement_texts":labels,"view_count":2,"unit":"mm","projection":"third",
               "projection_symbol_status":"absent","section_names":["A-A"]}
    ref=folder/"hybrid_test_plate_reference.json";ref.write_text(json.dumps(reference,indent=2,ensure_ascii=False),encoding="utf-8")
    return path,ref


if __name__=="__main__":
    print(*create(Path(__file__).resolve().parent/"files"),sep="\n")
