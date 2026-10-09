from echoguard.metrics import weighted_jaccard, set_jaccard, cosine_similarity, benjamini_hochberg

def test_weighted_jaccard():
    a={"A":0.9,"B":0.1}; b={"A":0.1,"B":0.9}
    assert abs(weighted_jaccard(a,b) - (0.2/1.8)) < 1e-9
    assert set_jaccard(a,b) == 1.0
    assert cosine_similarity(a,b) < 0.25

def test_bh():
    q=benjamini_hochberg([0.01,0.04,0.20])
    assert len(q)==3
    assert all(0 <= x <= 1 for x in q)
