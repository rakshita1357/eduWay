try:
    import google.generativeai as genai
    print('genai imported')
except Exception as e:
    print('Import error', e)
