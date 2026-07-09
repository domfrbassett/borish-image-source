#!/usr/bin/env python3
"""Animate accepted and obstruction-rejected paths in the concave L-room demo."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Iterable

import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation, FFMpegWriter, PillowWriter
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
import numpy as np


def parse_obj(path: Path):
    vertices=[]; faces=[]
    with path.open('r',encoding='utf-8',errors='replace') as h:
        for raw in h:
            line=raw.strip()
            if not line or line.startswith('#'): continue
            parts=line.split()
            if parts[0]=='v': vertices.append([float(parts[1]),float(parts[2]),float(parts[3])])
            elif parts[0]=='f':
                face=[]
                for token in parts[1:]:
                    ri=int(token.split('/')[0])
                    face.append(ri-1 if ri>0 else len(vertices)+ri)
                faces.append(tuple(face))
    return np.asarray(vertices,float),faces


def unique_edges(faces: Iterable[tuple[int,...]]):
    edges=set()
    for face in faces:
        for i,a in enumerate(face):
            b=face[(i+1)%len(face)]
            edges.add(tuple(sorted((a,b))))
    return sorted(edges)


def partial_polyline(points: np.ndarray, fraction: float):
    fraction=min(max(float(fraction),0.0),1.0)
    if len(points)<2: return points.copy()
    seg=np.diff(points,axis=0)
    lengths=np.linalg.norm(seg,axis=1)
    total=float(lengths.sum())
    if total<=0: return points[:1].copy()
    target=total*fraction
    out=[points[0]]
    for start,v,l in zip(points[:-1],seg,lengths):
        l=float(l)
        if target>=l:
            out.append(start+v); target-=l
        else:
            out.append(start+v*(target/max(l,1e-15))); break
    return np.asarray(out,float)


def set_equal(ax, xyz):
    mn=xyz.min(axis=0); mx=xyz.max(axis=0); centre=(mn+mx)/2
    span=mx-mn; radius=max(float(span.max())/2,0.5)*1.05
    ax.set_xlim(centre[0]-radius,centre[0]+radius)
    ax.set_ylim(centre[1]-radius,centre[1]+radius)
    ax.set_zlim(max(-0.25,centre[2]-radius),centre[2]+radius)
    ax.set_box_aspect((1,1,0.55))


def build(result_path: Path, audit_path: Path, obj_path: Path, output: Path, fps: int, duration: float, dpi: int):
    result=json.loads(result_path.read_text(encoding='utf-8'))
    audit=json.loads(audit_path.read_text(encoding='utf-8'))
    vertices,faces=parse_obj(obj_path)
    edges=unique_edges(faces)

    source=np.asarray(result['source'],float)
    receiver=np.asarray(result['receiver'],float)
    accepted=sorted(result['paths'],key=lambda p:(p['arrival_time_absolute_s'],p['order']))
    accepted_show=accepted[:12]
    rejected=audit['obstruction_rejections'][:6]
    direct=audit['direct_occlusion']

    fig=plt.figure(figsize=(10.5,7.5))
    ax=fig.add_subplot(111,projection='3d')

    polys=[[vertices[i] for i in f] for f in faces]
    shell=Poly3DCollection(polys,alpha=0.075,linewidths=0.35)
    ax.add_collection3d(shell)
    for a,b in edges:
        q=vertices[[a,b]]
        ax.plot(q[:,0],q[:,1],q[:,2],linewidth=1.0,alpha=0.55)

    ax.scatter([source[0]],[source[1]],[source[2]],marker='*',s=175,label='Source')
    ax.scatter([receiver[0]],[receiver[1]],[receiver[2]],marker='X',s=105,label='Receiver')

    direct_full=np.asarray([source,receiver])
    direct_line,=ax.plot([],[],[],linestyle='--',linewidth=2.3,alpha=0.8,label='Rejected / blocked')
    direct_marker,=ax.plot([],[],[],linestyle='',marker='o',markersize=5)
    direct_block,=ax.plot([],[],[],linestyle='',marker='X',markersize=10)

    rejected_lines=[]; rejected_markers=[]; rejected_blocks=[]; rejected_ghosts=[]
    for item in rejected:
        ghost,=ax.plot([],[],[],linestyle=':',linewidth=1.0,alpha=0.18)
        line,=ax.plot([],[],[],linestyle='--',linewidth=1.7,alpha=0.72)
        marker,=ax.plot([],[],[],linestyle='',marker='o',markersize=4)
        block,=ax.plot([],[],[],linestyle='',marker='X',markersize=8)
        rejected_ghosts.append(ghost); rejected_lines.append(line); rejected_markers.append(marker); rejected_blocks.append(block)

    accepted_lines=[]; accepted_markers=[]
    for p in accepted_show:
        line,=ax.plot([],[],[],linewidth=max(0.9,2.5-0.35*p['order']),alpha=0.72)
        marker,=ax.plot([],[],[],linestyle='',marker='o',markersize=3.5)
        accepted_lines.append(line); accepted_markers.append(marker)

    status=ax.text2D(0.02,0.965,'',transform=ax.transAxes)
    detail=ax.text2D(0.02,0.885,'',transform=ax.transAxes)
    summary=ax.text2D(0.02,0.04,
        f"Audit totals: {len(accepted)} accepted, {audit['solver_stats']['rejected_obstruction']} obstruction-rejected, "
        f"{audit['solver_stats']['rejected_visibility']} visibility-rejected",
        transform=ax.transAxes)

    ax.set_title('Borish image-source occlusion audit — concave L-shaped room')
    ax.set_xlabel('X (m)'); ax.set_ylabel('Y (m)'); ax.set_zlabel('Z (m)')
    ax.legend(loc='upper right')
    all_points=[vertices,source[None,:],receiver[None,:]]
    all_points.extend(np.asarray(p['path_vertices'],float) for p in accepted_show)
    set_equal(ax,np.vstack(all_points))

    nframes=max(2,int(round(duration*fps)))
    phase1=0.22; phase2=0.48

    def clear_artist(line): line.set_data_3d([],[],[])
    def set_line(line,p): line.set_data_3d(p[:,0],p[:,1],p[:,2])

    def update(i):
        progress=i/max(nframes-1,1)
        # Slow, subtle camera motion while keeping the notch readable.
        ax.view_init(elev=52-8*progress,azim=-68+20*progress)

        # Direct occlusion phase.
        if progress < phase1:
            local=progress/phase1
            block=np.asarray(direct['blocking_point'],float)
            dist_total=np.linalg.norm(receiver-source)
            dist_block=np.linalg.norm(block-source)
            frac_to_block=dist_block/max(dist_total,1e-12)
            travel=min(local/0.72,1.0)*frac_to_block
            q=partial_polyline(direct_full,travel)
            set_line(direct_line,q)
            end=q[-1]; direct_marker.set_data_3d([end[0]],[end[1]],[end[2]])
            if local>=0.72:
                direct_block.set_data_3d([block[0]],[block[1]],[block[2]])
            else: clear_artist(direct_block)
            status.set_text('Phase 1 — direct-path occlusion check')
            detail.set_text(
                'The source and receiver are in different wings.\n'
                f"Direct sound is rejected at {direct['blocking_patch_metadata']['group_name']} "
                f"({block[0]:.2f}, {block[1]:.2f}, {block[2]:.2f}) m."
            )
        else:
            block=np.asarray(direct['blocking_point'],float)
            set_line(direct_line,np.asarray([source,block]))
            clear_artist(direct_marker)
            direct_block.set_data_3d([block[0]],[block[1]],[block[2]])

        # Rejected candidate examples phase.
        if phase1 <= progress < phase2:
            local=(progress-phase1)/(phase2-phase1)
            index=min(int(local*len(rejected)),len(rejected)-1)
            within=local*len(rejected)-index
            for j,item in enumerate(rejected):
                full=np.asarray(item['candidate_path_vertices'],float)
                prefix=np.asarray(item['blocked_prefix_vertices'],float)
                if j<index:
                    set_line(rejected_ghosts[j],full); set_line(rejected_lines[j],prefix)
                    clear_artist(rejected_markers[j])
                    b=np.asarray(item['blocking_point'],float); rejected_blocks[j].set_data_3d([b[0]],[b[1]],[b[2]])
                elif j==index:
                    set_line(rejected_ghosts[j],full)
                    q=partial_polyline(prefix,min(within/0.82,1.0)); set_line(rejected_lines[j],q)
                    e=q[-1]; rejected_markers[j].set_data_3d([e[0]],[e[1]],[e[2]])
                    if within>=0.82:
                        b=np.asarray(item['blocking_point'],float); rejected_blocks[j].set_data_3d([b[0]],[b[1]],[b[2]])
                    else: clear_artist(rejected_blocks[j])
                else:
                    clear_artist(rejected_ghosts[j]); clear_artist(rejected_lines[j]); clear_artist(rejected_markers[j]); clear_artist(rejected_blocks[j])
            item=rejected[index]
            ancestry=' → '.join(x.get('group_name','?') for x in item['ancestry'])
            status.set_text(f'Phase 2 — obstruction rejection {index+1}/{len(rejected)}')
            detail.set_text(
                f"Candidate ancestry: {ancestry}\n"
                f"Rejected on segment {item['blocked_segment_index']+1}; blocker: "
                f"{item['blocking_patch_metadata'].get('group_name','?')}"
            )
        elif progress>=phase2:
            for j,item in enumerate(rejected):
                full=np.asarray(item['candidate_path_vertices'],float)
                prefix=np.asarray(item['blocked_prefix_vertices'],float)
                set_line(rejected_ghosts[j],full); set_line(rejected_lines[j],prefix)
                clear_artist(rejected_markers[j])
                b=np.asarray(item['blocking_point'],float); rejected_blocks[j].set_data_3d([b[0]],[b[1]],[b[2]])

        # Accepted paths phase: replay physical arrival ordering, scaled for viewing.
        if progress>=phase2:
            local=(progress-phase2)/(1-phase2)
            physical_end=max(float(p['arrival_time_absolute_s']) for p in accepted_show)
            physical_start=min(float(p['arrival_time_absolute_s']) for p in accepted_show)
            # Start the wave at t=0 and hold the final state briefly.
            travel=min(local/0.82,1.0)
            current=physical_end*travel
            arrived=0
            for j,p in enumerate(accepted_show):
                points=np.asarray(p['path_vertices'],float)
                arrival=float(p['arrival_time_absolute_s'])
                frac=min(max(current/max(arrival,1e-12),0.0),1.0)
                q=partial_polyline(points,frac); set_line(accepted_lines[j],q)
                if frac<1:
                    e=q[-1]; accepted_markers[j].set_data_3d([e[0]],[e[1]],[e[2]])
                else:
                    clear_artist(accepted_markers[j]); accepted_lines[j].set_alpha(0.38); arrived+=1
            status.set_text('Phase 3 — accepted reflection paths')
            detail.set_text(
                f"Showing the 12 earliest of {len(accepted)} accepted paths.\n"
                f"Scaled propagation time: {current*1000:5.1f} ms; arrivals shown: {arrived}/12."
            )
        else:
            for line,marker in zip(accepted_lines,accepted_markers): clear_artist(line); clear_artist(marker)

        return [direct_line,direct_marker,direct_block,*rejected_ghosts,*rejected_lines,*rejected_markers,*rejected_blocks,*accepted_lines,*accepted_markers,status,detail,summary]

    anim=FuncAnimation(fig,update,frames=nframes,interval=1000/fps,blit=False)
    output.parent.mkdir(parents=True,exist_ok=True)
    if output.suffix.lower()=='.gif':
        anim.save(output,writer=PillowWriter(fps=fps),dpi=dpi)
    elif output.suffix.lower()=='.mp4':
        anim.save(output,writer=FFMpegWriter(fps=fps,codec='libx264',bitrate=2600,extra_args=['-pix_fmt','yuv420p']),dpi=dpi)
    else: raise ValueError('output must be .gif or .mp4')
    plt.close(fig)


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--result',type=Path,default=Path(__file__).with_name('concave_run.json'))
    ap.add_argument('--audit',type=Path,default=Path(__file__).with_name('concave_occlusion_diagnostics.json'))
    ap.add_argument('--obj',type=Path,default=Path(__file__).with_name('borish_concave_L_room.obj'))
    ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--fps',type=int,default=24)
    ap.add_argument('--duration',type=float,default=14.0)
    ap.add_argument('--dpi',type=int,default=100)
    a=ap.parse_args()
    build(a.result,a.audit,a.obj,a.output,a.fps,a.duration,a.dpi)
    print(a.output.resolve())

if __name__=='__main__': main()
