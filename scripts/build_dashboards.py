"""Generate version-controlled Grafana dashboards; no running Grafana required."""
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
INTERVAL=30  # Keep in sync with prometheus.yml scrape_interval and portal.integrations.INTERVAL_SECONDS.
def panel(title,expression,index,unit='percent',kind='timeseries',legend='{{server}} GPU {{gpu}}'):
    return {'id':index+1,'title':title,'type':kind,'datasource':{'type':'prometheus','uid':'prometheus'},
            'gridPos':{'h':8,'w':12,'x':(index%2)*12,'y':(index//2)*8},
            'targets':[{'expr':expression,'refId':'A','legendFormat':legend}],
            'fieldConfig':{'defaults':{'unit':unit,'min':0},'overrides':[]},
            'options':{'legend':{'displayMode':'list','placement':'bottom'},'tooltip':{'mode':'multi'}}}

def dashboard(uid,title,panels,detail=False):
    output={'uid':uid,'title':title,'schemaVersion':39,'version':1,'editable':False,'timezone':'Asia/Seoul',
            'refresh':'30s','time':{'from':'now-24h','to':'now'},'tags':['gpu-lab','soft-reservation'],'panels':panels}
    if detail:
        output['templating']={'list':[{'name':'server','label':'워크스테이션','type':'query',
            'datasource':{'type':'prometheus','uid':'prometheus'},'query':'label_values(up{job="dcgm-exporter"}, server)',
            'refresh':1,'includeAll':True,'allValue':'.*','multi':True,'current':{'text':'All','value':'$__all'}}]}
    path=ROOT/'grafana/dashboards'/f'{uid}.json'
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

overview=[
    ('Exporter online', 'up{job=~"node-exporter|dcgm-exporter"}', 'short', 'stat','{{server}} {{job}}'),
    ('GPU utilization', 'DCGM_FI_DEV_GPU_UTIL', 'percent'),
    ('CPU utilization', '100 - avg by(server)(rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100','percent'),
    ('RAM utilization', '100 * (1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)','percent'),
    ('Disk utilization', '100*(1-node_filesystem_avail_bytes{fstype!~"tmpfs|overlay|squashfs",mountpoint=~"/|/data|/scratch"}/node_filesystem_size_bytes{fstype!~"tmpfs|overlay|squashfs",mountpoint=~"/|/data|/scratch"})','percent','timeseries','{{server}} {{mountpoint}}'),
    ('GPU VRAM used', 'DCGM_FI_DEV_FB_USED * 1048576','bytes'),
    ('GPU VRAM free', 'DCGM_FI_DEV_FB_FREE * 1048576','bytes'),
    ('GPU temperature', 'DCGM_FI_DEV_GPU_TEMP < 1000','celsius'),
    ('GPU power', 'DCGM_FI_DEV_POWER_USAGE < 10000','watt'),
    ('RAM used', 'node_memory_MemTotal_bytes - node_memory_MemAvailable_bytes','bytes'),
]
dashboard('gpu-lab-overview','GPU Lab · Overview',[panel(item[0],item[1],i,*item[2:]) for i,item in enumerate(overview)])
detail=[
    ('GPU utilization','DCGM_FI_DEV_GPU_UTIL{server=~"$server"}','percent'),
    ('GPU VRAM used','DCGM_FI_DEV_FB_USED{server=~"$server"} * 1048576','bytes'),
    ('GPU temperature','DCGM_FI_DEV_GPU_TEMP{server=~"$server"} < 1000','celsius'),
    ('GPU power','DCGM_FI_DEV_POWER_USAGE{server=~"$server"} < 10000','watt'),
    ('Compute processes by Linux user','count by(server,gpu,user)(lab_gpu_process_info{server=~"$server"} == 1)','short','timeseries','{{server}} GPU {{gpu}} {{user}}'),
    ('VRAM by compute process','lab_gpu_process_memory_mib{server=~"$server"} * 1048576','bytes','timeseries','{{server}} GPU {{gpu}} {{user}}'),
    ('7-day mean utilization','lab:gpu_utilization:avg7d{server=~"$server"}','percent'),
    ('30-day mean utilization','lab:gpu_utilization:avg30d{server=~"$server"}','percent'),
    ('Reserved hours (30d)',f'sum_over_time(lab_gpu_reserved{{server=~"$server"}}[30d]) * {INTERVAL} / 3600','h','bargauge'),
    ('Busy hours during reservations (30d)',f'sum_over_time(lab_gpu_reserved_busy{{server=~"$server"}}[30d]) * {INTERVAL} / 3600','h','bargauge'),
    ('Observed GPU-hours by Linux user (30d)',f'sum by(user) (count_over_time(lab:gpu_user_active:max{{server=~"$server"}}[30d])) * {INTERVAL} / 3600','h','bargauge','{{user}}'),
    ('GPU XID errors','DCGM_FI_DEV_XID_ERRORS{server=~"$server"} < 100000','short'),
    ('Process collector success','lab_gpu_process_collector_success{server=~"$server"}','short','stat','{{server}}'),
]
dashboard('gpu-lab-detail','GPU Lab · Server detail',[panel(item[0],item[1],i,*item[2:]) for i,item in enumerate(detail)],True)
print('Generated Overview and Server detail dashboards.')
