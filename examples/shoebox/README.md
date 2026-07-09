# Shoebox reference example

Room dimensions: 10 m × 8 m × 3 m.

Source: `(2, 3, 1.2)` m  
Receiver: `(6, 5, 1.2)` m

Run on Windows:

```powershell
.\run_shoebox.ps1
```

Expected order-2 result:

- 1 direct path
- 6 first-order paths
- 18 second-order paths
- 25 total accepted paths
- 36 reflected virtual-source nodes
