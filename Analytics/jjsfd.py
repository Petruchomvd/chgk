with open('Successful games', 'r', encoding='utf-8') as f:
    s = f.readlines()

listnum = []

for string in s:
    string = string.replace('{', "").replace('}', "").split(',')
    print(string[0][5:])
    listnum.append(int(string[0][5:]))
print(listnum)