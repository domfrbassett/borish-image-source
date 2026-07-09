from pathlib import Path

OUT = Path(__file__).resolve().parent
obj = OUT / 'borish_concave_L_room.obj'
mtl = OUT / 'borish_concave_L_room.mtl'
materials = OUT / 'borish_concave_materials.json'

# Counter-clockwise L-shaped footprint. Interior is left of every edge.
poly = [(0.0,0.0),(12.0,0.0),(12.0,4.0),(5.0,4.0),(5.0,10.0),(0.0,10.0)]
h = 3.0

verts=[]
faces=[]
def add_face(points, group, material):
    idx=[]
    for p in points:
        verts.append(p)
        idx.append(len(verts))
    faces.append((idx,group,material))

# Split the horizontal faces into rectangles so no concave OBJ polygon is fan-triangulated.
# Floor normals point down.
for name,(xmin,xmax,ymin,ymax) in [
    ('Floor_South',(0,12,0,4)),
    ('Floor_NorthWest',(0,5,4,10)),
]:
    add_face([(xmin,ymin,0),(xmin,ymax,0),(xmax,ymax,0),(xmax,ymin,0)], name, 'Floor')
# Ceiling normals point up.
for name,(xmin,xmax,ymin,ymax) in [
    ('Ceiling_South',(0,12,0,4)),
    ('Ceiling_NorthWest',(0,5,4,10)),
]:
    add_face([(xmin,ymin,h),(xmax,ymin,h),(xmax,ymax,h),(xmin,ymax,h)], name, 'Ceiling')

# Vertical walls. For CCW footprint, bottom_i,bottom_j,top_j,top_i points outward.
wall_names = ['South_Wall','East_Wall','Notch_South_Wall','Notch_East_Wall','North_Wall','West_Wall']
for i,(a,b) in enumerate(zip(poly, poly[1:]+poly[:1])):
    (x1,y1),(x2,y2)=a,b
    add_face([(x1,y1,0),(x2,y2,0),(x2,y2,h),(x1,y1,h)], wall_names[i], 'Wall')

with obj.open('w',encoding='utf-8') as f:
    f.write('# Borish concave L-shaped test enclosure, units: metres\n')
    f.write('mtllib borish_concave_L_room.mtl\n')
    f.write('o Borish_Concave_L_Room\n')
    for x,y,z in verts:
        f.write(f'v {x:g} {y:g} {z:g}\n')
    for ids,group,material in faces:
        f.write(f'g {group}\nusemtl {material}\n')
        f.write('f ' + ' '.join(map(str,ids)) + '\n')

mtl.write_text('''# Display-only MTL\nnewmtl Wall\nKd 0.72 0.72 0.72\nnewmtl Floor\nKd 0.46 0.42 0.36\nnewmtl Ceiling\nKd 0.90 0.90 0.90\n''',encoding='utf-8')
materials.write_text('''{\n  "default_absorption": [0.05,0.05,0.05,0.05,0.05,0.06,0.08,0.10],\n  "by_material": {\n    "Wall": [0.05,0.04,0.04,0.04,0.05,0.06,0.08,0.10],\n    "Floor": [0.15,0.12,0.10,0.08,0.07,0.07,0.08,0.10],\n    "Ceiling": [0.10,0.08,0.07,0.06,0.05,0.05,0.06,0.08]\n  }\n}\n''',encoding='utf-8')
print(obj)
