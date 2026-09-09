import pymupdf

doc = pymupdf.open(r"C:\Users\Admin\Desktop\books\Understanding human nature -- Adler, Alfred New York, USA, 1946.pdf")

print(doc.metadata)

doc.close()