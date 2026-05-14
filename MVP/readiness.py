import json

def readiness_score():
    with open("evidence.json","r",encoding="utf-8") as f:
        evi = json.load(f)
    score = 0
    if evi["nginx"]: score += 40
    if evi["wazuh"]: score += 40
    if len(evi["nginx"])+len(evi["wazuh"])>=2: score +=20
    return min(score,100)

if __name__=="__main__":
    s = readiness_score()
    print(f"数据就绪度：{s}/100")
    with open("readiness.json","w",encoding="utf-8") as f:
        json.dump({"score":s},f,indent=2)