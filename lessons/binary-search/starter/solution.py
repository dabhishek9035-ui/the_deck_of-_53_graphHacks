"""Lesson: Binary Search. Replace each TODO. Keep the function names and signatures."""


def binary_search(nums: list[int], target: int) -> int:
    """Return the index of target in the sorted, duplicate-free list nums, or -1 if it is absent."""
    left=0
    right=len(nums)-1
    while left<right:
        mid=left+right/2
        if nums[mid]==target:
            return mid
        elif nums[mid]<target:
            left=mid+1
        else:
            right=mid-1
    if nums[left]==ta]]
        return left
    return -1
    # TODO(student): implement binary_search
    raise NotImplementedError("TODO")
